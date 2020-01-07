from sklearn.utils import resample
import numpy as np
import pandas as pd
import pickle
import os
import json
import sys
from sklearn.utils import shuffle
from sklearn.utils.class_weight import compute_class_weight
from absl import logging
from . import transformations as transf
from ..models import bert2
from ..models.bert2.tokenization.custom_albert_tokenization import CustomAlbertTokenizer
from tensorflow.keras.preprocessing.sequence import pad_sequences
from .flags import FLAGS


class XLNetExample():
    def __init__(self, text_a, label, guid, text_b=None):
        self.text_a = text_a
        self.label = label
        self.guid = guid
        self.text_b = text_b


class Dataset:
    x = []
    y = []

    def __init__(self, x, y, random_state):
        self.x = x
        self.y = y

        self.random_state = random_state
        self.shuffle()

    def shuffle(self):
        self.x, self.y = shuffle(self.x, self.y, random_state=self.random_state)

    def get_length(self):
        xlen, ylen = len(self.x), len(self.y)
        if xlen != ylen:
            raise ValueError("size of x != size of y ({} != {})".format(xlen, ylen))
        return xlen


class DataLoader:
    def __init__(self, train_data=None, val_data=None, test_data=None):
        assert FLAGS.cs_num_classes == 2 or FLAGS.cs_num_classes == 3

        self.data, self.eval_data, = self.load_ext_data(train_data, val_data, test_data) \
            if not FLAGS.cs_use_clef_data else self.load_clef_data()

        if FLAGS.cs_use_clef_data and FLAGS.cs_combine_ours_clef_data:
            ours_data, ours_eval = self.load_ext_data(train_data, val_data, test_data)

            ours_data = self.convert_3_to_2(ours_data)
            ours_eval = self.convert_3_to_2(ours_eval)

            self.data.x += ours_data.x
            self.data.y += ours_data.y
            self.eval_data.x += ours_eval.x
            self.eval_data.y += ours_eval.y

        if FLAGS.cs_num_classes == 2 and not FLAGS.cs_use_clef_data:
            self.data = self.convert_3_to_2(self.data)
            self.eval_data = self.convert_3_to_2(self.data)

        self.class_weights = self.compute_class_weights()
        logging.info('Class weights computed to be {}'.format(self.class_weights))

        self.data.shuffle()
        self.post_process_flags()

    @staticmethod
    def convert_3_to_2(data):
        if FLAGS.cs_alt_two_class_combo:
            data.y = [(0 if data.y[i] == 0 else 1) for i in range(len(data.y))]
        else:
            data.y = [(1 if data.y[i] == 2 else 0) for i in range(len(data.y))]

        return data

    def compute_class_weights(self):
        ret = compute_class_weight('balanced', [z for z in range(FLAGS.cs_num_classes)], self.data.y)

        if FLAGS.cs_num_classes == 3:
            ret[1] /= 4

        return ret

    def load_training_data(self):
        ret = Dataset(self.data.x, self.data.y, FLAGS.cs_random_state)

        if FLAGS.cs_sklearn_oversample:
            classes = [[] for _ in range(FLAGS.cs_num_classes)]

            for i in range(len(ret.x)):
                classes[ret.y[i]].append(ret.x[i])

            if FLAGS.cs_num_classes == 3:
                maj_len = len(classes[2])
                classes[0] = resample(classes[0], n_samples=int(maj_len * 2.75), random_state=FLAGS.cs_random_state)
                classes[1] = resample(classes[1], n_samples=int(maj_len * 0.90), random_state=FLAGS.cs_random_state)
                classes[2] = resample(classes[2], n_samples=int(maj_len * 1.50), random_state=FLAGS.cs_random_state)
            else:
                pass
                # maj_len = len(classes[0])
                # # classes[0] = resample(classes[0], n_samples=int(maj_len), random_state=FLAGS.cs_random_state)
                # classes[1] = resample(classes[1], n_samples=int(maj_len * 0.40), random_state=FLAGS.cs_random_state)

            ret = Dataset([], [], random_state=FLAGS.cs_random_state)
            del self.data.x[:FLAGS.cs_train_examples]
            del self.data.y[:FLAGS.cs_train_examples]

            for lab in range(len(classes)):
                for inp_x in classes[lab]:
                    ret.x.append(inp_x)
                    ret.y.append(lab)

                    self.data.x.insert(0, inp_x)
                    self.data.y.insert(0, lab)

            FLAGS.cs_total_examples += ret.get_length() - FLAGS.cs_train_examples
            FLAGS.cs_train_examples = ret.get_length()

        ret.shuffle()

        return ret

    def load_testing_data(self):
        ret = Dataset([], [], FLAGS.cs_random_state)

        for i in range(FLAGS.cs_test_examples):
            ret.x.append(self.eval_data.x[i])
            ret.y.append(self.eval_data.y[i])

        return ret

    def post_process_flags(self):
        FLAGS.cs_train_examples = self.data.get_length()
        FLAGS.cs_test_examples = self.eval_data.get_length()
        FLAGS.cs_total_examples = FLAGS.cs_train_examples + FLAGS.cs_test_examples

    @staticmethod
    def load_clef_data():
        if not os.path.isfile(FLAGS.cs_prc_clef_loc):
            FLAGS.cs_refresh_data = True

        def read_from_file(loc):
            df = pd.read_csv(loc)
            ret_txt, ret_lab = [row['text'] for idx, row in df.iterrows()], [row['label'] for idx, row in df.iterrows()]
            return ret_txt, ret_lab

        if FLAGS.cs_refresh_data:
            train_txt, train_lab = read_from_file(FLAGS.cs_raw_clef_train_loc)
            eval_txt, eval_lab = read_from_file(FLAGS.cs_raw_clef_test_loc)

            train_features, eval_features = DataLoader.process_text_for_transformers(train_txt, eval_txt)

            logging.info('Loading preprocessing dependencies')
            transf.load_dependencies()

            logging.info('Processing train data')
            train_txt, _, train_sent = transf.process_dataset(train_txt)
            logging.info('Processing eval data')
            eval_txt, _, eval_sent = transf.process_dataset(eval_txt)

            train_features = DataLoader.convert_data_to_tensorflow_format(train_features)
            eval_features = DataLoader.convert_data_to_tensorflow_format(eval_features)

            train_data = Dataset(list(map(list, zip(train_features.tolist(), train_sent))), train_lab,
                                 random_state=FLAGS.cs_random_state)
            eval_data = Dataset(list(map(list, zip(eval_features.tolist(), eval_sent))), eval_lab,
                                random_state=FLAGS.cs_random_state)

            with open(FLAGS.cs_prc_clef_loc, 'wb') as f:
                pickle.dump((train_data, eval_data), f)
            logging.info('Refreshed data, successfully dumped at {}'.format(FLAGS.cs_prc_clef_loc))
        else:
            logging.info('Restoring data from {}'.format(FLAGS.cs_prc_clef_loc))
            with open(FLAGS.cs_prc_clef_loc, 'rb') as f:
                train_data, eval_data = pickle.load(f)

        return train_data, eval_data

    @staticmethod
    def load_ext_data(train_data_in, val_data_in, test_data_in):
        data_loc = FLAGS.cs_prc_data_loc[:-7] + '_{}'.format(FLAGS.cs_tfm_type) + '.pickle'

        if (train_data_in is not None and val_data_in is not None and test_data_in is not None) or \
           (not os.path.isfile(data_loc)):
            FLAGS.cs_refresh_data = True

        if FLAGS.cs_refresh_data:
            train_data = DataLoader.parse_json(FLAGS.cs_raw_data_loc) if train_data_in is None else train_data_in
            dj_eval_data = DataLoader.parse_json(FLAGS.cs_raw_dj_eval_loc) if test_data_in is None else test_data_in

            train_txt = [z[0] for z in train_data]
            eval_txt = [z[0] for z in dj_eval_data]

            train_lab = [z[1] + 1 for z in train_data]
            eval_lab = [z[1] + 1 for z in dj_eval_data]

            logging.info('Loading preprocessing dependencies')
            transf.load_dependencies()

            logging.info('Processing train data')
            train_txt, _, train_sent = transf.process_dataset(train_txt)
            logging.info('Processing eval data')
            eval_txt, _, eval_sent = transf.process_dataset(eval_txt)

            train_features, eval_features = DataLoader.process_text_for_transformers(train_txt, eval_txt)

            train_features = DataLoader.convert_data_to_tensorflow_format(train_features)
            eval_features = DataLoader.convert_data_to_tensorflow_format(eval_features)

            train_data = Dataset(list(map(list, zip(train_features.tolist(), train_sent))), train_lab,
                                 random_state=FLAGS.cs_random_state)
            eval_data = Dataset(list(map(list, zip(eval_features.tolist(), eval_sent))), eval_lab,
                                random_state=FLAGS.cs_random_state)

            with open(data_loc, 'wb') as f:
                pickle.dump((train_data, eval_data), f)
            logging.info('Refreshed data, successfully dumped at {}'.format(data_loc))
        else:
            logging.info('Restoring data from {}'.format(data_loc))
            with open(data_loc, 'rb') as f:
                train_data, eval_data = pickle.load(f)

        return train_data, eval_data

    @staticmethod
    def convert_data_to_tensorflow_format(features):
        return DataLoader.pad_seq(features)

    @staticmethod
    def process_text_for_transformers(train_txt, eval_txt):
        if FLAGS.cs_tfm_type == 'bert':
            vocab_file = os.path.join(FLAGS.cs_model_loc, "vocab.txt")
            tokenizer = bert2.bert_tokenization.FullTokenizer(vocab_file, do_lower_case=True)
            train_txt = [tokenizer.convert_tokens_to_ids(tokenizer.tokenize(x)) for x in train_txt]
            eval_txt = [tokenizer.convert_tokens_to_ids(tokenizer.tokenize(x)) for x in eval_txt]
        else:
            tokenizer = CustomAlbertTokenizer()
            train_txt = tokenizer.tokenize_array(train_txt)
            eval_txt = tokenizer.tokenize_array(eval_txt)

        return train_txt, eval_txt

    @staticmethod
    def parse_json(json_loc):
        with open(json_loc) as f:
            temp_data = json.load(f)

        dl = []
        labels = [0, 0, 0]

        for el in temp_data:
            lab = int(el["label"])
            txt = el["text"]

            labels[lab + 1] += 1
            dl.append([txt, lab])

        print('{}: {}'.format(json_loc, labels))
        return dl

    @staticmethod
    def pad_seq(inp, ver=0):  # 0 is int, 1 is string
        return pad_sequences(inp, padding="post", maxlen=FLAGS.cs_max_len) if ver == 0 else \
            pad_sequences(inp, padding="post", maxlen=FLAGS.cs_max_len, dtype='str', value='')
