# coding=utf-8
#
# created by kpe on 28.Mar.2019 at 12:33
#

from __future__ import absolute_import, division, print_function

import tensorflow as tf
import params_flow as pf

from .layer import Layer
from .embeddings import BertEmbeddingsLayer
from .transformer import TransformerEncoderLayer
from .pooler import PoolerLayer

K = tf.keras


class BertModelLayer(Layer):
    """
    Implementation of BERT (arXiv:1810.04805), adapter-BERT (arXiv:1902.00751) and ALBERT (arXiv:1909.11942).

    See: https://arxiv.org/pdf/1810.04805.pdf - BERT
         https://arxiv.org/pdf/1902.00751.pdf - adapter-BERT
         https://arxiv.org/pdf/1909.11942.pdf - ALBERT

    """

    class Params(BertEmbeddingsLayer.Params,
                 TransformerEncoderLayer.Params):
        pass

    # noinspection PyUnusedLocal
    def _construct(self, params: Params):
        self.embeddings_layer = None
        self.encoders_layer = None

        self.support_masking = True

    # noinspection PyAttributeOutsideInit
    def build(self, input_shape):
        if isinstance(input_shape, list):
            assert len(input_shape) == 2
            input_ids_shape, token_type_ids_shape = input_shape
            self.input_spec = [K.layers.InputSpec(shape=input_ids_shape),
                               K.layers.InputSpec(shape=token_type_ids_shape)]
        else:
            input_ids_shape = input_shape
            self.input_spec = K.layers.InputSpec(shape=input_ids_shape)

        self.embeddings_layer = BertEmbeddingsLayer.from_params(
            self.params,
            name="embeddings"
        )

        # create all transformer encoder sub-layers
        self.encoders_layer = TransformerEncoderLayer.from_params(
            self.params,
            name="encoder"
        )

        self.dropout_layer = K.layers.Dropout(rate=self.params.hidden_dropout)
        self.pooler_layer = PoolerLayer(self.params.hidden_size, name="pooler")

        super(BertModelLayer, self).build(input_shape)

    def apply_adapter_freeze(self):
        """ Should be called once the model has been built to freeze
        all bet the adapter and layer normalization layers in BERT.
        """
        if self.params.adapter_size is not None:
            def freeze_selector(layer):
                return layer.name not in ["adapter-up", "adapter-down", "LayerNorm", "extra_word_embeddings"]

            pf.utils.freeze_leaf_layers(self, freeze_selector)

    def call(self, inputs, perturb=None, get_embedding=-1, mask=None, training=None):
        if mask is None:
            mask = self.embeddings_layer.compute_mask(inputs)

        out = self.embeddings_layer(inputs, mask=mask, training=training, get_embedding=get_embedding, perturb=perturb)
        embedding_output, ret_embed = out[0], out[1]

        output = self.encoders_layer(embedding_output, mask=mask, training=training)
        output = self.dropout_layer(output, training=training)

        pooled_output = self.pooler_layer(tf.squeeze(output[:, 0:1, :], axis=1), training=training)

        if get_embedding == -1:
            return pooled_output
        else:
            return ret_embed, pooled_output
