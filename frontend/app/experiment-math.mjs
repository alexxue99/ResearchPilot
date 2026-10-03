import katex from 'katex';

const commands = new Set(['sum', 'sigma', 'alpha', 'beta', 'gamma', 'delta', 'lambda',
  'rho', 'theta', 'epsilon', 'omega', 'phi', 'Phi', 'pi', 'zeta', 'eta', 'mu', 'nu',
  'xi', 'psi', 'tau', 'chi', 'kappa', 'Sigma', 'Lambda', 'Gamma', 'Omega', 'Theta',
  'Delta', 'infinity', 'sin', 'cos', 'log', 'max', 'min']);
const functions = new Set(['sqrt', 'diag', 'mean', 'argmax', 'Var', 'Re']);
const notationWords = new Set(['bar', 'in']);

function mathematicalIdentifier(value) {
  const base = value.split('_')[0];
  return /^[A-Za-z]$/.test(base) || /^[A-Z][a-z]$/.test(base) ||
    /^[a-z]hat$/.test(base) || base === 'x0' || commands.has(base) ||
    functions.has(base) || notationWords.has(base);
}

function identifierTex(value) {
  const [base, ...indices] = value.split('_');
  let result = base === 'infinity' ? '\\infty' : base === 'in' ? '\\in' :
    notationWords.has(base) ? base : commands.has(base) ? '\\' + base :
    functions.has(base) ? '\\operatorname{' + base + '}' : base;
  if (/^[a-z]hat$/.test(base)) result = '\\widehat{' + base[0] + '}';
  if (base === 'x0') result = 'x_0';
  if (indices[0] === 'hat') {
    result = '\\widehat{' + result + '}';
    indices.shift();
  } else if (indices[0] === 'star') {
    result += '^{*}';
    indices.shift();
  }
  if (indices.length) {
    const indexTex = indices.map(index => commands.has(index) ? '\\' + index :
      /^[A-Za-z0-9]$/.test(index) || /^\d+$/.test(index) ? index : '\\text{' + index + '}');
    result += '_{' + indexTex.join(',') + '}';
  }
  // A control word must not merge with the next variable, e.g. \lambda a.
  return /^\\[A-Za-z]+$/.test(result) ? result + ' ' : result;
}

// Older experiment records contain escaped TeX commands and, in a few cases,
// a control character where the leading backslash of \sigma should be.
function plainFormulaTex(formula) {
  // Match function parentheses by depth; sums inside a square root can nest.
  let converted = formula;
  for (let start = converted.lastIndexOf('sqrt('); start >= 0; start = converted.lastIndexOf('sqrt(')) {
    let depth = 1, end = start + 5;
    for (; end < converted.length && depth; end++) {
      if (converted[end] === '(') depth++;
      if (converted[end] === ')') depth--;
    }
    if (depth) break;
    converted = converted.slice(0, start) + '\\sqrt{' + converted.slice(start + 5, end - 1) + '}' + converted.slice(end);
  }
  return converted
    .replace(/\bbar\s+([A-Za-z])(?=_|\^|\b)/g, '\\bar{$1}')
    .replace(/(\\bar\{[A-Za-z]\})_([A-Za-z0-9]+)/g, (_, symbol, index) =>
      symbol + '_{' + (commands.has(index) ? '\\' + index : /^\d+$/.test(index) ? index : '\\text{' + index + '}') + '}')
    .replace(/\bin\b/g, '\\in ')
    .replace(/(?<!\\)\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*/g, value =>
      mathematicalIdentifier(value) ? identifierTex(value) : value)
    .replace(/\|\|([^|]+)\|\|/g, '\\lVert $1 \\rVert ')
    .replace(/\b([a-zA-Z])\*/g, '$1^{*}')
    .replace(/\^\(([^()]*)\)/g, '^{($1)}')
    .replace(/<-|<=|>=/g, operator => ({'<-': '\\leftarrow ', '<=': '\\le ', '>=': '\\ge '}[operator]))
    .replace(/\.\.\./g, '\\ldots ')
    .replace(/(\\[A-Za-z]+) +(?=[_^])/g, '$1');
}

export function delimitPlainExperimentMath(text) {
  // Infer only recognizable mathematical syntax, never arbitrary prose or setting keys.
  const start = /\b(?:(?:sqrt|diag|sin|cos|log|zeta|Var|Re)(?=\()|(?:bar\s+[A-Z])|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*(?=\s*(?:[(_^=<>]|\*\s*=)))|\|\||\(/g;
  const token = /\s+|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*|\d+(?:\.\d+)?|<-|<=|>=|\.\.\.|[{}()_^*/=+<>|,\-]/y;
  let result = '', previous = 0, found;
  while ((found = start.exec(text))) {
    // Do not begin inside a code identifier or a bare TeX command.
    if (found.index && /[A-Za-z0-9_\\{_^=+\-*/]/.test(text[found.index - 1])) continue;
    let end = found.index;
    token.lastIndex = end;
    const groups = [];
    let next;
    while ((next = token.exec(text))) {
      const value = next[0];
      if (/^[A-Za-z]/.test(value) && !mathematicalIdentifier(value)) break;
      if (value === '(' || value === '{') groups.push(value);
      if (value === ')' || value === '}') {
        if (groups.at(-1) !== (value === ')' ? '(' : '{')) break;
        groups.pop();
      }
      end = token.lastIndex;
    }
    const candidate = text.slice(found.index, end).trimEnd().replace(/[,]+$/, '');
    if (groups.length && /^[A-Za-z]/.test(text[found.index])) {
      // An unsupported token inside a function or grouped expression should not
      // cause the scanner to reinterpret its inner fragments as separate math.
      const pending = [...groups];
      for (let i = end; i < text.length; i++) {
        const ch = text[i];
        if (ch === '(' || ch === '{') pending.push(ch);
        else if (ch === ')' || ch === '}') {
          if (pending.at(-1) === (ch === ')' ? '(' : '{')) pending.pop();
          if (!pending.length) {
            start.lastIndex = i + 1;
            break;
          }
        }
        if (i === text.length - 1) start.lastIndex = text.length;
      }
      continue;
    }
    if (!candidate || groups.length || !/[=^_]|\|\||sqrt\(|diag\(|<-/.test(candidate)) continue;
    if (candidate === '||' || (candidate.match(/\|\|/g)?.length ?? 0) % 2) {
      start.lastIndex = Math.max(start.lastIndex, end);
      continue;
    }
    // Standalone setting names belong to prose; only infer them inside a formula.
    if (/^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$/.test(candidate) &&
        candidate.split('_').some((part, index) => index && part.length > 1 && part !== 'star')) {
      start.lastIndex = end;
      continue;
    }
    const listedNames = candidate.split(/\s*,\s*/);
    if (listedNames.length > 1 &&
        listedNames.every(name => /^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+$/.test(name)) &&
        listedNames.some(name => name.split('_').slice(1).some(part => part.length > 1 && part !== 'star'))) {
      // Do not turn a comma-separated list of code/settings names into one equation.
      break;
    }
    const tex = plainFormulaTex(candidate).trimEnd();
    try {
      katex.renderToString(tex, {throwOnError: true, strict: 'ignore'});
    } catch {
      // Preserve ambiguous or incomplete input instead of introducing a parse error.
      start.lastIndex = Math.max(start.lastIndex, end);
      continue;
    }
    result += text.slice(previous, found.index) + '$' + tex + '$';
    previous = found.index + candidate.length;
    start.lastIndex = previous;
  }
  return result + text.slice(previous);
}

export function flattenExperimentSettings(value, prefix = '') {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return Object.entries(value).flatMap(([key, item]) =>
      flattenExperimentSettings(item, [prefix, key.replaceAll('_', ' ')].filter(Boolean).join(' / ')));
  }
  return [{label: prefix, value: Array.isArray(value) ? value.map(String).join(', ') : String(value ?? 'Not specified')}];
}

export function normalizeExperimentMath(text, {inferPlainMath = false} = {}) {
  // Keep literal code examples intact while accepting common model TeX delimiters.
  return text.split(/(`{3,}[\s\S]*?`{3,}|`[^`\n]*`)/g).map((part, index) => {
    if (index % 2) return part;
    const delimited = part
      .replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `\n\n$$\n${math.trim()}\n$$\n\n`)
      .replace(/\\\(([^\n]*?)\\\)/g, (_, math) => `$${math}$`);
    const prepared = inferPlainMath ? delimited.split(/(\$\$[\s\S]*?\$\$|\$[^$\n]+\$)/g)
      .map((part, index) => index % 2 ? part : delimitPlainExperimentMath(part)).join('') : delimited;
    return prepared.replace(/\$\$([\s\S]*?)\$\$|\$([^$\n]+)\$/g, match =>
    match
      .replace(/\u0001(?=[A-Za-z])/g, '\\')
      .replace(/\\{2,}(?=[A-Za-z{}|])/g, '\\')
    );
  }).join('');
}
