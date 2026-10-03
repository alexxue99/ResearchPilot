import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import {normalizeExperimentMath} from '../app/experiment-math.mjs';

test('assessment math accepts TeX delimiters and preserves literal code', () => {
  const report = String.raw`Expected mode $ (1 - \\sigma_i^2 / \\lVert A \\rVert_F^2)^k $.
Inline \(x^2\).
\[\sum_i \sigma_i^2\]`;
  const normalized = normalizeExperimentMath(report);
  const html = renderToStaticMarkup(React.createElement(ReactMarkdown,
    {remarkPlugins: [remarkGfm, remarkMath], rehypePlugins: [rehypeKatex]}, normalized));
  assert.doesNotMatch(html, /katex-error/);
  assert.match(html, /class="katex-display"/);
  assert.match(html, /<mi>σ<\/mi>/);
  const code = '`\\(x^2\\)`\n```tex\n$\\\\sigma_i$\n```';
  assert.equal(normalizeExperimentMath(code), code);
});

test('report Markdown renders inline and display LaTeX', () => {
  const report = `Inline $x^2$ and display:

$$
\\sum_{i=1}^n i
$$`;
  const html = renderToStaticMarkup(React.createElement(ReactMarkdown,
    {remarkPlugins: [remarkGfm, remarkMath], rehypePlugins: [rehypeKatex]}, report));
  assert.match(html, /class="katex"/);
  assert.match(html, /class="katex-display"/);
  assert.match(html, /<msup>/);
});

test('saved experiment math renders escaped commands without visible delimiters or KaTeX errors', () => {
  const savedText = 'Profile $\x01sigma_i \\\\propto 1/i$ with adaptive $\\\\rho$ and valid $\\lambda$.';
  const html = renderToStaticMarkup(React.createElement(ReactMarkdown,
    {remarkPlugins: [remarkGfm, remarkMath], rehypePlugins: [rehypeKatex]},
    normalizeExperimentMath(savedText)));
  assert.doesNotMatch(html, /katex-error|\u0001|\$/);
  assert.match(html, /<mi>σ<\/mi>/);
  assert.match(html, /<mi>ρ<\/mi>/);
  assert.match(html, /<mi>λ<\/mi>/);
});

test('algorithm settings keep math commands after JSON stringification', () => {
  const settings = JSON.stringify({method: 'adaptive $\\\\rho$ with $\\\\sigma_i$'});
  const html = renderToStaticMarkup(React.createElement(ReactMarkdown,
    {remarkPlugins: [remarkGfm, remarkMath], rehypePlugins: [rehypeKatex]},
    normalizeExperimentMath(settings)));
  assert.doesNotMatch(html, /katex-error|\$/);
  assert.match(html, /<mi>ρ<\/mi>/);
  assert.match(html, /<mi>σ<\/mi>/);
});
