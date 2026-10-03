import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import {normalizeExperimentMath, flattenExperimentSettings} from '../app/experiment-math.mjs';

function render(text) {
  return renderToStaticMarkup(React.createElement(ReactMarkdown,
    {remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex]},
    normalizeExperimentMath(text, {inferPlainMath: true})));
}

test('saved pseudocode renders square roots, sums, singular values and matrix construction', () => {
  const step = 'Set the flat singular values to 1 and the harmonic singular values to sqrt(8 / sum_{i=1}^8 1/i^2) / i for i=1,...,8. Form A = U diag(sigma) V^T for each profile; both matrices then have Frobenius norm sqrt(8).';
  const html = render(step);
  assert.doesNotMatch(html, /katex-error/);
  assert.match(html, /<msqrt>/);
  assert.match(html, /<mo>∑<\/mo>/);
  assert.match(html, /<mi>σ<\/mi>/);
  assert.match(html, /for each profile/);
  const visibleHtml = html.replace(/<annotation\b[^>]*>[\s\S]*?<\/annotation>/g, '');
  assert.doesNotMatch(visibleHtml, />[^<]*sqrt\(|>[^<]*sum_/);
});

test('saved norms and Kaczmarz update render as mathematical notation', () => {
  const html = render('Compute p_j = ||a_j||_2^2 / ||A||_F^2. Update x <- x + ((b_j - a_j^T x) / ||a_j||_2^2) a_j.');
  assert.doesNotMatch(html, /katex-error/);
  assert.match(html, /←/);
  assert.match(html, /<msubsup>/);
});

test('parenthesized decay factors render completely, including outer powers', () => {
  for (const formula of ['(1 - sigma^2 / ||A||_F^2)', '(1 - sigma^2 / ||A||_F^2)^k']) {
    const normalized = normalizeExperimentMath(`Expected factor ${formula}, supporting slower decay.`, {inferPlainMath: true});
    assert.equal(normalized, `Expected factor $${formula.replace('sigma', '\\sigma').replace('||A||', '\\lVert A \\rVert')}$, supporting slower decay.`);
    const html = render(`Expected factor ${formula}, supporting slower decay.`);
    assert.doesNotMatch(html, /katex-error/);
    assert.match(html, /<mi>σ<\/mi>/);
    assert.match(html, /supporting slower decay/);
    const visibleHtml = html.replace(/<annotation\b[^>]*>[\s\S]*?<\/annotation>/g, '');
    assert.doesNotMatch(visibleHtml, /sigma\^|\|\|A\|\|/);
  }
});

test('formulas inside prose parentheses do not swallow the closing punctuation', () => {
  assert.equal(normalizeExperimentMath('(with sigma^2)', {inferPlainMath: true}), '(with $\\sigma^2$)');
  assert.equal(normalizeExperimentMath('(eight paired comparisons)', {inferPlainMath: true}), '(eight paired comparisons)');
});

test('nested settings expose each formula instead of stringifying JSON', () => {
  const rows = flattenExperimentSettings({profiles: {
    flat: {singular_values: 'sigma_i=1 for i=1,...,8'},
    harmonic: {singular_values: 'sigma_i=sqrt(8/sum_{j=1}^8(1/j^2))/i for i=1,...,8'},
  }, seeds: [0, 1], dimension: 8});
  assert.equal(rows[1].label, 'profiles / harmonic / singular values');
  assert.equal(rows[2].value, '0, 1');
  assert.equal(rows[3].value, '8');
  const html = render(rows[1].value);
  assert.doesNotMatch(html, /katex-error/);
  assert.match(html, /<msqrt>/);
  assert.match(html, /<mo>∑<\/mo>/);
});

test('prose, existing math, code, and underscore setting keys are preserved', () => {
  const text = 'Use independent row-norm-proportional sampling with fixed random seeds.';
  assert.equal(normalizeExperimentMath(text, {inferPlainMath: true}), text);
  assert.equal(normalizeExperimentMath('$\\sigma_i=1$ and `sqrt(8)`', {inferPlainMath: true}), '$\\sigma_i=1$ and `sqrt(8)`');
  assert.equal(normalizeExperimentMath('global_wall_clock_cap_seconds', {inferPlainMath: true}), 'global_wall_clock_cap_seconds');
});

test('whole parameter names remain prose instead of dangling subscripts', () => {
  const text = 'For each seed, generate n_train training inputs and n_test test inputs. For each m in m_values and lambda in lambda_values, fit a_hat.';
  assert.equal(normalizeExperimentMath(text, {inferPlainMath: true}), text);
  assert.doesNotMatch(render(text), /katex-error/);
  assert.equal(normalizeExperimentMath('||unknown_name||', {inferPlainMath: true}), '||unknown_name||');
  for (const text of ['x_', 'x^', 'sqrt(2/m', 'x_{i']) {
    assert.doesNotMatch(render(text), /katex-error/);
  }
});

test('saved Fourier regression objective and metric render as complete formulas', () => {
  const objective = '(1/n_train)||Phi_train a-y_train||_2^2+lambda||a||_2^2';
  const metric = 'E_{s,lambda}(m)=(1/n_test)||Phi_test a_hat-y_test||_2^2';
  for (const formula of [objective, metric]) {
    const normalized = normalizeExperimentMath(formula, {inferPlainMath: true});
    assert.match(normalized, /n_\{\\text\{(?:train|test)\}\}/);
    assert.match(normalized, /\\Phi_\{\\text\{(?:train|test)\}\}/);
    const html = render(formula);
    assert.doesNotMatch(html, /katex-error|\\rVerta/);
  }
  assert.match(normalizeExperimentMath(metric, {inferPlainMath: true}), /\\widehat\{a\}/);
  const html = render('||a||b+lambda a');
  assert.doesNotMatch(html, /katex-error|\\rVertb|\\lambdaa/);
});

test('full failing pseudocode step no longer creates partial math or joined commands', () => {
  const text = 'For each lambda in lambda_values, fit a_hat by minimizing (1/n_train)||Phi_train a-y_train||_2^2+lambda||a||_2^2; use the minimum-norm least-squares solution when lambda=0 and a_hat=(Phi_train^T Phi_train/n_train+lambda I_m)^(-1)Phi_train^T y_train/n_train when lambda>0.';
  const normalized = normalizeExperimentMath(text, {inferPlainMath: true});
  assert.match(normalized, /lambda_values, fit a_hat/);
  assert.match(normalized, /\\widehat\{a\}=/);
  assert.match(normalized, /\^\{\(-1\)\}/);
  assert.doesNotMatch(render(text), /katex-error|\\rVerta/);
});
