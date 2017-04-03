import os
from os.path import join

import numpy as np
import pandas as pd
import time
from sacred import Experiment
from sacred.observers import MongoObserver
from sklearn.externals.joblib import Memory
from sklearn.externals.joblib import dump
from sklearn.externals.joblib import load
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder

from modl.classification import make_loadings_extractor, FactoredLogistic
from modl.datasets import get_data_dirs
from modl.input_data.fmri.unmask import build_design, retrieve_components
from modl.model_selection import StratifiedGroupShuffleSplit
from modl.utils.system import get_cache_dirs

idx = pd.IndexSlice

predict_contrast = Experiment('predict_contrast')
collection = predict_contrast.path

global_artifact_dir = join(get_data_dirs()[0], 'pipeline', 'contrast',
                           'prediction')

observer = MongoObserver.create(db_name='amensch', collection=collection)
predict_contrast.observers.append(observer)


# observer = FileStorageObserver.create(basedir=global_artifact_dir)
# predict_contrast.observers.append(observer)


@predict_contrast.config
def config():
    dictionary_penalty = 1e-4
    n_components_list = [16, 64, 256]

    from_loadings = True
    loadings_dir = join(get_data_dirs()[0], 'pipeline', 'contrast', 'reduced')

    datasets = ['archi', 'hcp']
    n_subjects = 788

    test_size = dict(hcp=0.1, archi=0.5)
    dataset_weight = dict(hcp=1, archi=10)
    train_size = None

    validation = True

    factored = False

    max_samples = int(1e6)
    alpha = 0.0001
    beta = 0.0  # Factored only
    latent_dim = 100  # Factored only
    activation = 'linear'  # Factored only
    dropout = 0.6  # Factored only
    batch_size = 200
    early_stop = False

    penalty = 'trace'  # Non-factored only
    tol = 1e-7  # Non-factored only

    projection = True

    fine_tune = 0.2

    standardize = True
    scale_importance = 'sqrt'
    multi_class = 'ovr'  # Non-factored only

    fit_intercept = True
    identity = False
    refit = False  # Non-factored only

    n_jobs = 24
    verbose = 2
    seed = 10

    hcp_unmask_contrast_dir = join(get_data_dirs()[0], 'pipeline',
                                   'unmask', 'contrast', 'hcp', '23')
    archi_unmask_contrast_dir = join(get_data_dirs()[0], 'pipeline',
                                     'unmask', 'contrast', 'archi', '30')
    datasets_dir = {'archi': archi_unmask_contrast_dir,
                    'hcp': hcp_unmask_contrast_dir}

    del hcp_unmask_contrast_dir
    del archi_unmask_contrast_dir


@predict_contrast.automain
def run(dictionary_penalty,
        alpha,
        beta,
        latent_dim,
        n_components_list,
        dataset_weight,
        batch_size,
        max_samples, n_jobs,
        test_size,
        train_size,
        dropout,
        early_stop,
        identity,
        fit_intercept,
        n_subjects,
        scale_importance,
        standardize,
        datasets,
        datasets_dir,
        factored,
        activation,
        from_loadings,
        loadings_dir,
        projection,
        validation,
        fine_tune,
        verbose,
        _run,
        _seed):
    artifact_dir = join(global_artifact_dir,
                        str(_run._id), '_artifacts')
    if not os.path.exists(artifact_dir):
        os.makedirs(artifact_dir)

    memory = Memory(cachedir=get_cache_dirs()[0], verbose=2)

    if verbose:
        print('Fetch data')
    if not from_loadings:
        X, masker = memory.cache(build_design)(datasets,
                                               datasets_dir,
                                               n_subjects)
        # Add a dataset column to the X matrix
        datasets = X.index.get_level_values('dataset').values
        datasets = pd.Series(index=X.index, data=datasets, name='dataset')
        X = pd.concat([X, datasets], axis=1)

        labels = X.index.get_level_values('contrast').values
        label_encoder = LabelEncoder()
        labels = label_encoder.fit_transform(labels)
        y = pd.Series(index=X.index, data=labels, name='label')
    else:
        loadings_dir = join(loadings_dir, str(projection))
        masker = load(join(loadings_dir, 'masker.pkl'))
        X = load(join(loadings_dir, 'Xt.pkl'))
        X = X.loc[idx[datasets, :, :, :, :]]
        y = load(join(loadings_dir, 'y.pkl'))
        y = y.loc[idx[datasets, :, :, :, :]]
        label_encoder = load(join(loadings_dir, 'label_encoder.pkl'))

    if verbose:
        print('Split data')
    cv = StratifiedGroupShuffleSplit(stratify_name='dataset',
                                     group_name='subject',
                                     test_size=test_size,
                                     train_size=train_size,
                                     n_splits=1,
                                     random_state=_seed)
    train, test = next(cv.split(X))

    y_train = y.iloc[train]

    if validation:
        cv = StratifiedGroupShuffleSplit(stratify_name='dataset',
                                         group_name='subject',
                                         test_size=.1,
                                         train_size=None,
                                         n_splits=1,
                                         random_state=_seed)
        sub_train, val = next(cv.split(y_train))

        sub_train = train[sub_train]
        val = train[val]

        X_train = X.iloc[sub_train]
        y_train = y.iloc[sub_train]
        X_val = X.iloc[val]
        y_val = y.iloc[val]
        train = sub_train

    if verbose:
        print('Transform and fit data')
    pipeline = []
    if not from_loadings:
        if verbose:
            print('Retrieve components')
        components = memory.cache(retrieve_components)(dictionary_penalty,
                                                       masker,
                                                       n_components_list)
        if projection:
            transformer = make_loadings_extractor(components,
                                                  standardize=standardize,
                                                  scale_importance=scale_importance,
                                                  identity=identity,
                                                  scale_bases=True,
                                                  n_jobs=n_jobs,
                                                  memory=memory)
        pipeline.append(('transformer', transformer))
    classifier = FactoredLogistic(optimizer='adam',
                                  max_samples=max_samples,
                                  activation=activation,
                                  fit_intercept=fit_intercept,
                                  latent_dim=latent_dim if factored else None,
                                  dropout=dropout,
                                  alpha=alpha,
                                  early_stop=early_stop,
                                  fine_tune=fine_tune,
                                  beta=beta,
                                  batch_size=batch_size,
                                  n_jobs=n_jobs,
                                  verbose=verbose)
    pipeline.append(('classifier', classifier))
    estimator = Pipeline(pipeline, memory=memory)

    if from_loadings:
        if validation:
            Xt_val = X_val.values
        X_train = X_train.values
    else:
        Xt_val = transformer.fit_transform(X_val, y_val)

    t0 = time.time()
    if validation:
        estimator.fit(X_train, y_train,
                      classifier__validation_data=(Xt_val, y_val),
                      classifier__dataset_weight=dataset_weight
                      )
    else:
        estimator.fit(X_train, y_train)
        print('Fit time: %.2f' % (time.time() - t0))

    predicted_labels = estimator.predict(X.values)
    predicted_labels = label_encoder.inverse_transform(predicted_labels)
    labels = label_encoder.inverse_transform(y)

    prediction = pd.DataFrame({'true_label': labels,
                               'predicted_label': predicted_labels},
                              index=X.index)

    if validation:
        prediction = pd.concat([prediction.iloc[train],
                                prediction.iloc[val],
                                prediction.iloc[test]],
                               names=['fold'], keys=['train', 'val', 'test'])
    else:
        prediction = pd.concat([prediction.iloc[train],
                                prediction.iloc[test]],
                               names=['fold'], keys=['train', 'test'])
    prediction.sort_index()
    match = prediction['true_label'] == prediction['predicted_label']

    _run.info['n_epochs'] = estimator.named_steps['classifier'].n_epochs_
    if verbose:
        print('Compute score')
    for fold, sub_match in match.groupby(level='fold'):
        _run.info['%s_score' % fold] = np.mean(sub_match)
    for (fold, dataset), sub_match in match.groupby(level=['fold', 'dataset']):
        _run.info['%s_%s_score' % (fold, dataset)] = np.mean(sub_match)
    if verbose:
        print('Write task prediction artifacts')
    prediction.to_csv(join(artifact_dir, 'prediction.csv'))
    _run.add_artifact(join(artifact_dir, 'prediction.csv'),
                      name='prediction.csv')

    dump(label_encoder, join(artifact_dir, 'label_encoder.pkl'))
    dump(estimator, join(artifact_dir, 'estimator.pkl'))
