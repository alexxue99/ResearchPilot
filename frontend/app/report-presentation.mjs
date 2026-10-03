import React from 'react';

function citationKey(value) {
  try {
    const url = new URL(value);
    if (/(^|\.)arxiv\.org$/i.test(url.hostname)) {
      return `arxiv:${url.pathname.replace(/^\/(abs|pdf)\//, '').replace(/\.pdf$/, '').replace(/v\d+$/, '')}`;
    }
    if (/(^|\.)doi\.org$/i.test(url.hostname)) return `doi:${decodeURIComponent(url.pathname.slice(1)).toLowerCase()}`;
    if (url.protocol === 'arxiv:') return decodeURIComponent(value).replace(/v\d+$/, '');
    if (url.protocol === 'doi:') return decodeURIComponent(value).toLowerCase();
    return `${url.origin}${url.pathname}`.replace(/\/$/, '');
  } catch {
    return value?.replace(/v\d+$/, '');
  }
}

export function ReportLink({href, children, sources = []}) {
  const key = citationKey(href);
  const source = sources.find(item => [item.url, item.id,
    item.arxiv_id && `arxiv:${item.arxiv_id}`, item.doi && `doi:${item.doi.toLowerCase()}`]
    .filter(Boolean).some(alias => citationKey(alias) === key));
  const external = /^https?:\/\//i.test(href ?? '');
  const title = source?.title?.trim();
  const validTitle = title && !/^(?:https?:\/\/|doi:|arxiv:)/i.test(title);
  return React.createElement('a', {href, target: external ? '_blank' : undefined,
    rel: external ? 'noopener noreferrer' : undefined}, validTitle ? title : children);
}

export function spaceReportSummary(text) {
  // Protect inline formatting so sentence breaks cannot split its delimiters.
  const protectedParts = [];
  const protectedText = text.replace(/\[[^\]]*\]\([^)]*\)|`[^`]*`|\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\*\*[^*\n]+\*\*|\*[^*\n]+\*/g,
    match => { protectedParts.push(match); return `\uE000${protectedParts.length - 1}\uE001`; });
  const segmenter = new Intl.Segmenter('en', {granularity: 'sentence'});
  return protectedText.split(/\n\s*\n/).map(paragraph => {
    const sentences = [...segmenter.segment(paragraph)].map(item => item.segment.trim()).filter(Boolean);
    const groups = [];
    for (let index = 0; index < sentences.length; index += 2) groups.push(sentences.slice(index, index + 2).join(' '));
    return groups.join('\n\n');
  }).join('\n\n').replace(/\uE000(\d+)\uE001/g, (_, index) => protectedParts[Number(index)]);
}
