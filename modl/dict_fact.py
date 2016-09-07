import numpy as np
from scipy import linalg
from sklearn.base import BaseEstimator
from sklearn.utils import check_array
from sklearn.utils import check_random_state

from modl._utils.enet_proj import enet_scale
from .dict_fact_fast import DictFactImpl

max_int = np.iinfo(np.uint32).max

class DictFact(BaseEstimator):
    def __init__(self,
                 n_components=30,
                 alpha=1.0,
                 l1_ratio=0,
                 pen_l1_ratio=0,
                 tol=1e-3,
                 # Hyper-parameters
                 learning_rate=1.,
                 batch_size=1,
                 offset=0,
                 sample_learning_rate=None,
                 # Reduction parameter
                 reduction=1,
                 solver='gram',  # ['average', 'gram', 'masked']
                 weights='sync',  # ['sync', 'async']
                 subset_sampling='random',  # ['random', 'cyclic']
                 dict_reduction='follow',
                 # ['independent', 'coupled']
                 # Dict parameter
                 dict_init=None,
                 # For variance reduction
                 n_samples=None,
                 # Generic parameters
                 max_n_iter=0,
                 n_epochs=1,
                 random_state=None,
                 verbose=0,
                 n_threads=1,
                 callback=None,
                 **kwargs
                 ):
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.offset = offset
        self.sample_learning_rate = sample_learning_rate

        self.reduction = reduction
        self.alpha = alpha
        self.l1_ratio = l1_ratio
        self.pen_l1_ratio = pen_l1_ratio
        self.tol = tol

        self.dict_init = dict_init
        self.n_components = n_components

        self.solver = solver
        self.subset_sampling = subset_sampling
        self.dict_reduction = dict_reduction
        self.weights = weights

        self.max_n_iter = max_n_iter
        self.n_epochs = n_epochs

        self.n_samples = n_samples

        self.random_state = random_state
        self.verbose = verbose

        self.n_threads = n_threads

        self.callback = callback

    @property
    def initialized(self):
        return hasattr(self, '_impl')

    @property
    def A(self):
        return np.array(self._impl.A)

    @property
    def B(self):
        return np.array(self._impl.B)

    @property
    def G(self):
        return np.array(self._impl.G)

    @property
    def G_average(self):
        return np.array(self._impl.G_average)

    @property
    def Dx_average(self):
        return np.array(self._impl.Dx_average)

    @property
    def D(self):
        return np.array(self._impl.D)

    @property
    def code(self):
        return np.array(self._impl.code)

    @property
    def total_counter(self):
        return np.array(self._impl.total_counter)

    @property
    def sample_counter(self):
        return np.array(self._impl.sample_counter)

    @property
    def feature_counter(self):
        return np.array(self._impl.feature_counter)

    @property
    def scaled_D(self):
        return np.array(self._impl.scaled_D())

    def _initialize(self, X):
        """Initialize statistic and dictionary"""
        n_samples, n_features = X.shape

        # Magic
        if self.n_samples is not None:
            n_samples = self.n_samples

        random_state = check_random_state(self.random_state)
        if self.dict_init is not None:
            if self.dict_init.shape != (self.n_components, n_features):
                raise ValueError(
                    'Initial dictionary and X shape mismatch: %r != %r' % (
                        self.dict_init.shape,
                        (self.n_components, n_features)))
            D = check_array(self.dict_init, order='F',
                            dtype='float', copy=True)
        else:
            D = np.empty((self.n_components, n_features), order='F')
            D[:] = random_state.randn(self.n_components, n_features)

        D = enet_scale(D, l1_ratio=self.l1_ratio, radius=1)

        params = self._get_impl_params()
        random_seed = random_state.randint(max_int)
        self._impl = DictFactImpl(D, n_samples,
                                  n_threads=self.n_threads,
                                  random_seed=random_seed,
                                  **params)

    def _update_impl_params(self):
        self._impl.set_impl_params(**self._get_impl_params())

    def _get_impl_params(self):
        solver = {
            'masked': 1,
            'gram': 2,
            'average': 3,
        }
        weights = {
            'sync': 1,
            'async_freq': 2,
            'async_prob': 3
        }
        subset_sampling = {
            'random': 1,
            'cyclic': 2,
        }
        if self.dict_reduction == 'follow':
            dict_reduction = 0
        elif self.dict_reduction == 'same':
            dict_reduction = self.reduction
        else:
            dict_reduction = self.dict_reduction

        if self.sample_learning_rate is None:
            sample_learning_rate = 2.5 - 2 * self.learning_rate
        else:
            sample_learning_rate = self.sample_learning_rate

        res = {'alpha': self.alpha,
               "l1_ratio": self.l1_ratio,
               'pen_l1_ratio': self.pen_l1_ratio,
               'tol': self.tol,

               'learning_rate': self.learning_rate,
               'sample_learning_rate': sample_learning_rate,
               'offset': self.offset,
               'batch_size': self.batch_size,

               'solver': solver[self.solver],
               'weights': weights[self.weights],
               'subset_sampling': subset_sampling[self.subset_sampling],
               'dict_reduction': dict_reduction,
               'reduction': self.reduction,
               'verbose': self.verbose,
               'callback': None if self.callback is None else lambda:
               self.callback(self)}
        return res

    def set_params(self, **params):
        if self.initialized:
            if 'n_samples' in params:
                raise ValueError('Cannot reset attribute n_samples after'
                                 'initialization')
            if 'n_threads' in params:
                raise ValueError('Cannot reset attribute n_threads after'
                                 'initialization')
            BaseEstimator.set_params(self, **params)
            self._update_impl_params()
        else:
            BaseEstimator.set_params(self, **params)

    def partial_fit(self, X, sample_indices=None, check_input=None):
        if sample_indices is None:
            sample_indices = np.arange(X.shape[0], dtype='i4')
        if not self.initialized or check_input is None:
            check_input = True
        if check_input:
            X = check_array(X, dtype='float', order='C')
        if not self.initialized:
            self._initialize(X)
        if self.max_n_iter > 0:
            remaining_iter = self.max_n_iter - self._impl.total_counter
            X = X[:remaining_iter]
        self._impl.partial_fit(X, sample_indices)
        return self

    def fit(self, X, y=None):
        """Use X to learn a dictionary Q_. The algorithm cycles on X
        until it reaches the max number of iteration

        Parameters
        ----------
        X: ndarray (n_samples, n_features)
            Dataset to learn the dictionary from
        """
        X = check_array(X, dtype='float', order='C')
        self._initialize(X)
        sample_indices = np.arange(X.shape[0], dtype='i4')
        if self.max_n_iter > 0:
            while self._impl.total_counter < self.max_n_iter:
                self.partial_fit(X, sample_indices=sample_indices,
                                 check_input=False)
        else:
            for i in range(self.n_epochs):
                self.partial_fit(X, sample_indices=sample_indices,
                                 check_input=False)
        return self

    def transform(self, X, y=None):
        if not self.initialized:
            raise ValueError()
        X = check_array(X, dtype='float64', order='C')
        # if self.pen_l1_ratio != 0:
        code, scaled_D = self._impl.transform(X)
        return np.asarray(code), np.asarray(scaled_D)
        # else:
        #     D = self.scaled_D
        #     Dx = (X.dot(D.T)).T
        #     G = D.dot(D.T).T
        #     G.flat[::self.n_components + 1] += self.alpha
        #     code = linalg.solve(G, Dx, sym_pos=True,
        #                         overwrite_a=True, check_finite=False)
        #     return code.T, D

    def score(self, X):
        code, scaled_D = self.transform(X)
        loss = np.sum((X - code.dot(self.scaled_D)) ** 2) / 2
        norm1_code = np.sum(np.abs(code))
        norm2_code = np.sum(code ** 2)
        regul = self.alpha * (norm1_code * self.pen_l1_ratio
                              + (1 - self.pen_l1_ratio) * norm2_code / 2)
        return (loss + regul) / X.shape[0]

    @property
    def components_(self):
        return self.scaled_D