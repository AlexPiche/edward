from __future__ import print_function
import numpy as np
import tensorflow as tf

from blackbox.data import Data

class Inference:
    """
    Base class for inference methods.

    Arguments
    ----------
    model: Model
        probability model p(x, z)
    variational: Variational
        variational model q(z; lambda)
    data: Data, optional
        data x
    """
    def __init__(self, model, variational, data=Data()):
        self.model = model
        self.variational = variational
        self.data = data

    def run(self, n_iter=1000, n_minibatch=1, n_data=None,
            n_print=100, score=None):
        """
        Run inference algorithm.

        Arguments
        ----------
        n_iter: int, optional
            Number of iterations for optimization.
        n_minibatch: int, optional
            Number of samples from variational model for calculating
            stochastic gradients.
        n_data: int, optional
            Number of samples for data subsampling. Default is to use all
            the data.
        n_print: int, optional
            Number of iterations for each print progress.
        score: bool, optional
            Whether to force inference to use the score function
            gradient estimator. Otherwise default is to use the
            reparameterization gradient if available.
        """
        self.n_iter = n_iter
        self.n_minibatch = n_minibatch
        self.n_data = n_data
        self.n_print = n_print
        if score is None and hasattr(self.variational, 'reparam'):
            self.score = False
        else:
            self.score = True

        self.samples = tf.placeholder(shape=(self.n_minibatch, self.variational.num_vars),
                                      dtype=tf.float32,
                                      name='samples')
        self.elbos = tf.zeros([self.n_minibatch])

        if self.score:
            loss = self.build_score_loss()
        else:
            loss = self.build_reparam_loss()

        update = tf.train.AdamOptimizer(0.1).minimize(loss)
        init = tf.initialize_all_variables()

        sess = tf.Session()
        sess.run(init)
        for t in range(self.n_iter):
            if self.score:
                samples = self.variational.sample(self.samples.get_shape(), sess)
            else:
                samples = self.variational.sample_noise(self.samples.get_shape())

            _, elbo = sess.run([update, self.elbos], {self.samples: samples})

            self.print_progress(t, elbo, sess)

    def print_progress(self, t, elbos, sess):
        if t % self.n_print == 0:
            print("iter %d elbo %.2f " % (t, np.mean(elbos)))
            self.variational.print_params(sess)

    def build_score_loss(self):
        pass

    def build_reparam_loss(self):
        pass

class MFVI(Inference):
# TODO this isn't MFVI so much as VI where q is analytic
    """
    Mean-field variational inference
    (Ranganath et al., 2014; Kingma and Welling, 2014)
    """
    def __init__(self, *args, **kwargs):
        Inference.__init__(self, *args, **kwargs)

    def build_score_loss(self):
        """
        Loss function to minimize, whose gradient is a stochastic
        gradient based on the score function estimator.
        """
        if hasattr(self.variational, 'entropy'):
            # ELBO = E_{q(z; lambda)} [ log p(x, z) ] + H(q(z; lambda))
            # where entropy is analytic
            q_log_prob = tf.zeros([self.n_minibatch], dtype=tf.float32)
            for i in range(self.variational.num_vars):
                q_log_prob += self.variational.log_prob_zi(i, self.samples)

            p_log_prob = self.model.log_prob(x, self.samples)
            q_entropy = self.variational.entropy()
            self.elbos = p_log_prob + q_entropy
            return tf.reduce_mean(q_log_prob * \
                                  tf.stop_gradient(p_log_prob)) + \
                   q_entropy
        else:
            # ELBO = E_{q(z; lambda)} [ log p(x, z) - log q(z; lambda) ]
            q_log_prob = tf.zeros([self.n_minibatch], dtype=tf.float32)
            for i in range(self.variational.num_vars):
                q_log_prob += self.variational.log_prob_zi(i, self.samples)

            x = self.data.sample(self.n_data)
            self.elbos = self.model.log_prob(x, self.samples) - \
                         q_log_prob
            return -tf.reduce_mean(q_log_prob * tf.stop_gradient(self.elbos))

    def build_reparam_loss(self):
        """
        Loss function to minimize, whose gradient is a stochastic
        gradient based on the reparameterization trick.
        """
        z = self.variational.reparam(self.samples)

        if hasattr(self.variational, 'entropy'):
            # ELBO = E_{q(z; lambda)} [ log p(x, z) ] + H(q(z; lambda))
            # where entropy is analytic
            x = self.data.sample(self.n_data)
            self.elbos = tf.reduce_mean(self.model.log_prob(x, z)) + \
                         self.variational.entropy()
        else:
            # ELBO = E_{q(z; lambda)} [ log p(x, z) - log q(z; lambda) ]
            q_log_prob = tf.zeros([self.n_minibatch], dtype=tf.float32)
            for i in range(self.variational.num_vars):
                q_log_prob += self.variational.log_prob_zi(i, z)

            x = self.data.sample(self.n_data)
            self.elbos = tf.reduce_mean(
                self.model.log_prob(x, z) - q_log_prob)

        return -self.elbos

class HVI(Inference):
    """
    Variational inference with a hierarchical variational model
    (Ranganath et al., 2015)
    """
    def __init__(self, *args, **kwargs):
        Inference.__init__(self, *args, **kwargs)

    def build_score_loss(self):
        """
        Loss function to minimize, whose gradient is a stochastic
        gradient based on the score function estimator.
        (This is if q_mf uses score loss)
        """
        x = self.data.sample(self.n_data)
        # TODO
        # This comes after q_mf newly set the params from the previous
        # sampling. Maybe q_mf should abstract away from holding the
        # parameters then, or we should set them after having the
        # lambda_samples in this function.
        # TODO generalize to > 1 minibatch sample
        # ELBO = E_{q(z, lambda; theta)} [
        #   log p(x, z) - log q(z | lambda) - log q(lambda | theta) +
        #   log r(lambda | z, phi) ]
        q_mf_lpdf = tf.zeros([self.n_minibatch], dtype=tf.float32)
        for i in range(self.variational.num_vars):
            q_mf_lpdf += self.variational.q_mf.log_prob_zi(i, self.samples)

        # TODO
        # This comes after q_prior and r_post newly stores the lambda
        # samples from the previous sampling.
        q_prior_lpdf = self.variational.q_prior.log_prob()
        r_post_lpdf = self.variational.r_post.log_prob( \
            self.variational.q_prior.lambda_samples)

        self.elbos = self.model.log_prob(x, self.samples) - q_mf_lpdf
        loss_mf = q_mf_lpdf * tf.stop_gradient(self.elbos)

        loss_second = r_post_lpdf - q_prior_lpdf
        self.elbos += loss_second

        loss_third = q_mf_lpdf * tf.stop_gradient(r_post_lpdf)
        return -tf.reduce_mean(loss_mf + loss_second + loss_third)
        # TODO how to return gradient for phi as well?

    def build_reparam_loss(self):
        """
        Loss function to minimize, whose gradient is a stochastic
        gradient based on the reparameterization trick.
        (This is if q_mf uses reparam loss)
        """
        # TODO
        # The only difference is the first and third term
        pass

    # TODO if q_prior needs score loss
    # TODO ith gradient computations
