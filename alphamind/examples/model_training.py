# -*- coding: utf-8 -*-
"""
Created on 2017-8-24

@author: cheng.li
"""

import numpy as np
import pandas as pd
import copy
from sklearn.linear_model import *
from sklearn.ensemble import RandomForestRegressor
from sklearn.ensemble import AdaBoostRegressor
from sklearn.svm import NuSVR
from alphamind.api import *
from PyFin.api import *
from matplotlib import pyplot as plt

plt.style.use('ggplot')

'''
Settings:

universe     - zz500
neutralize   - all industries
benchmark    - zz500
base factors - all the risk styles
quantiles    - 5
start_date   - 2012-01-01
end_date     - 2017-08-01
re-balance   - 2 week
training     - every 8 week
'''

engine = SqlEngine('postgresql+psycopg2://postgres:A12345678!@10.63.6.220/alpha')
universe = Universe('zz500', ['zz500'])
neutralize_risk = industry_styles
portfolio_risk_neutralize = []
portfolio_industry_neutralize = True

alpha_factors = {
    'eps': LAST('eps_q'),
    'roe': LAST('roe_q'),
    'bdto': LAST('BDTO'),
    'cfinc1': LAST('CFinc1'),
    'chv': LAST('CHV'),
    'rvol': LAST('RVOL'),
    'val': LAST('VAL'),
    'grev': LAST('GREV'),
    'droeafternonorecurring': LAST('DROEAfterNonRecurring')}

benchmark = 905
n_bins = 5
frequency = '2w'
batch = 8
start_date = '2012-01-01'
end_date = '2017-11-05'
method = 'risk_neutral'
use_rank = 100

'''
fetch data from target data base and do the corresponding data processing
'''

data_package = fetch_data_package(engine,
                                  alpha_factors=alpha_factors,
                                  start_date=start_date,
                                  end_date=end_date,
                                  frequency=frequency,
                                  universe=universe,
                                  benchmark=benchmark,
                                  batch=batch,
                                  neutralized_risk=neutralize_risk,
                                  pre_process=[winsorize_normal, standardize],
                                  post_process=[winsorize_normal, standardize],
                                  warm_start=batch)

'''
training phase: using Linear - regression from scikit-learn
'''

train_x = data_package['train']['x']
train_y = data_package['train']['y']

dates = sorted(train_x.keys())

model_df = pd.Series()
features = data_package['x_names']

for train_date in dates:
    model = LinearRegression(features, fit_intercept=False)
    x = train_x[train_date]
    y = train_y[train_date]

    model.fit(x, y)
    model_df.loc[train_date] = model
    alpha_logger.info('trade_date: {0} training finished'.format(train_date))


'''
predicting phase: using trained model on the re-balance dates (optimizing with risk neutral)
'''

predict_x = data_package['predict']['x']
settlement = data_package['settlement']

industry_dummies = pd.get_dummies(settlement['industry'].values)
risk_styles = settlement[portfolio_risk_neutralize].values

final_res = np.zeros(len(dates))

for i, predict_date in enumerate(dates):
    model = model_df[predict_date]
    x = predict_x[predict_date]
    cons = Constraints()
    index = settlement.trade_date == predict_date
    benchmark_w = settlement[index]['weight'].values
    realized_r = settlement[index]['dx'].values
    industry_names = settlement[index]['industry'].values
    is_tradable = settlement[index]['isOpen'].values

    cons.add_exposure(['total'], np.ones((len(is_tradable), 1)))
    cons.set_constraints('total', benchmark_w.sum(), benchmark_w.sum())

    if portfolio_industry_neutralize:
        ind_exp = industry_dummies[index]

        risk_tags = ind_exp.columns
        cons.add_exposure(risk_tags, ind_exp.values)
        benchmark_exp = benchmark_w @ ind_exp.values

        for k, name in enumerate(risk_tags):
            cons.set_constraints(name, benchmark_exp[k], benchmark_exp[k])

    if portfolio_risk_neutralize:
        risk_exp = risk_styles[index]

        risk_tags = np.array(portfolio_risk_neutralize)
        cons.add_exposure(risk_tags, risk_exp)

        benchmark_exp = benchmark_w @ risk_exp
        for k, name in enumerate(risk_tags):
            cons.set_constraints(name, benchmark_exp[k], benchmark_exp[k])

    predict_y = model.predict(x)

    is_tradable[:] = True
    weights, analysis = er_portfolio_analysis(predict_y,
                                              industry_names,
                                              realized_r,
                                              constraints=cons,
                                              detail_analysis=True,
                                              benchmark=benchmark_w,
                                              is_tradable=is_tradable,
                                              method=method,
                                              use_rank=use_rank)

    final_res[i] = analysis['er']['total'] / benchmark_w.sum()
    alpha_logger.info('trade_date: {0} predicting finished'.format(predict_date))

last_date = advanceDateByCalendar('china.sse', dates[-1], frequency)

df = pd.Series(final_res, index=dates[1:] + [last_date])
df.sort_index(inplace=True)
df.cumsum().plot()
plt.title('Prod factors model {1} ({0})'.format(method, model.__class__.__name__))
plt.show()

