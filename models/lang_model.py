from . import bert2

import sys
sys.path.append('..')
from flags import FLAGS


class LanguageModel:
    def __init__(self):
        pass

    @staticmethod
    def build_bert():
        bert_params = bert2.params_from_pretrained_ckpt(FLAGS.bert_model_loc)
        return bert2.BertModelLayer.from_params(bert_params, name='bert')