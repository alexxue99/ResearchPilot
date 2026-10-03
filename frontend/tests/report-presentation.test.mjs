import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import {ReportLink, spaceReportSummary} from '../app/report-presentation.mjs';

test('paper links use known titles and retain their destination in a new tab', () => {
  const href = 'https://arxiv.org/pdf/2006.16978v2.pdf';
  const sources = [{id: 'arxiv:2006.16978', title: 'Article title', url: 'https://arxiv.org/abs/2006.16978'}];
  const html = renderToStaticMarkup(React.createElement(ReactMarkdown, {
    components: {a: props => React.createElement(ReportLink, {...props, sources})},
  }, `[${href}](${href})`));
  assert.match(html, />Article title<\/a>/);
  assert.match(html, /href="https:\/\/arxiv.org\/pdf\/2006.16978v2.pdf"/);
  assert.match(html, /target="_blank"/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test('unknown links keep their text and internal links stay in the current tab', () => {
  const html = renderToStaticMarkup(React.createElement(ReportLink, {href: '#details'}, 'Details'));
  assert.equal(html, '<a href="#details">Details</a>');
});

test('identifier-only sources match DOI and legacy arXiv destinations', () => {
  for (const [id, href] of [
    ['arxiv:math/0702226', 'https://arxiv.org/abs/math/0702226v1'],
    ['doi:10.1137/20m1350947', 'https://doi.org/10.1137/20M1350947'],
  ]) {
    const html = renderToStaticMarkup(React.createElement(ReportLink,
      {href, sources: [{id, title: 'Actual article title'}]}, href));
    assert.match(html, />Actual article title<\/a>/);
  }
});

test('a URL placeholder cannot override an existing article title link label', () => {
  const href = 'https://arxiv.org/abs/math/0702226';
  const html = renderToStaticMarkup(React.createElement(ReportLink,
    {href, sources: [{id: 'arxiv:math/0702226', title: href}]}, 'Actual article title'));
  assert.match(html, />Actual article title<\/a>/);
});

test('dense summaries gain paragraph breaks without breaking links, decimals, or math', () => {
  const text = 'The experiment needed 12.09 times more iterations. See [A. Paper](https://example.org/paper). '
    + 'The mode is $(1 - \\sigma_i^2)^k$. Evidence is limited. Further work remains.';
  const spaced = spaceReportSummary(text);
  assert.equal(spaced, 'The experiment needed 12.09 times more iterations. See [A. Paper](https://example.org/paper).\n\n'
    + 'The mode is $(1 - \\sigma_i^2)^k$. Evidence is limited.\n\nFurther work remains.');
  const html = renderToStaticMarkup(React.createElement(ReactMarkdown, {}, spaced));
  assert.equal((html.match(/<p>/g) ?? []).length, 3);
});

test('summary spacing preserves bold assessment labels and italic sentences', () => {
  const text = '**ResearchPilot assessment: likely true.** Literature contains supporting results. '
    + '*Experiments support the conjecture in the tested cases.* More evidence is needed.';
  const spaced = spaceReportSummary(text);
  const html = renderToStaticMarkup(React.createElement(ReactMarkdown, {}, spaced));
  assert.match(html, /<strong>ResearchPilot assessment: likely true\.<\/strong>/);
  assert.match(html, /<em>Experiments support the conjecture in the tested cases\.<\/em>/);
  assert.doesNotMatch(html, /\*/);
});
