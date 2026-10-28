'use strict';
// Static syntax and dependency inventory. Never loads or executes game scripts.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const [input, parserPath, output] = process.argv.slice(2);
if (!input || !parserPath || !output) {
  throw new Error('Usage: node tools/analyze_scripts.cjs INPUT PARSER_JS NEW_OUTPUT_JSON');
}
const parser = require(path.resolve(parserPath));
const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');
const files = [];
const calls = new Map();
function sourceFiles(directory, prefix = '') {
  return fs.readdirSync(directory, {withFileTypes: true}).flatMap(item => {
    const relative = path.join(prefix, item.name);
    return item.isDirectory() ? sourceFiles(path.join(directory, item.name), relative)
      : item.isFile() && item.name.endsWith('.lua') ? [relative] : [];
  });
}
for (const name of sourceFiles(input).sort()) {
  const bytes = fs.readFileSync(path.join(input, name));
  const item = { name, sha256: sha256(bytes), syntax_lua53: 'failed', calls: [] };
  try {
    const ast = parser.parse(bytes.toString('latin1'), {luaVersion: '5.3', scope: true});
    item.syntax_lua53 = 'passed';
    const namedCalls = new Map();
    const visit = value => {
      if (!value || typeof value !== 'object') return;
      if (value.type === 'CallExpression' && value.base.type === 'Identifier'
          && value.base.isLocal === false) {
        const id = value.base.name;
        namedCalls.set(id, (namedCalls.get(id) || 0) + 1);
      }
      for (const [key, child] of Object.entries(value)) {
        if (key === 'globals') continue;
        if (Array.isArray(child)) child.forEach(visit);
        else if (child && typeof child === 'object') visit(child);
      }
    };
    visit(ast);
    item.calls = [...namedCalls].sort((a,b) => a[0].localeCompare(b[0]));
    for (const [symbol, count] of item.calls) {
      if (!calls.has(symbol)) calls.set(symbol, {symbol, count: 0, files: []});
      const row = calls.get(symbol);
      row.count += count;
      row.files.push(name);
    }
  } catch (error) { item.error = String(error.message); }
  files.push(item);
}
const report = {
  parser: {name: 'luaparse', version: parser.version,
    sha256: sha256(fs.readFileSync(parserPath)), language: 'Lua 5.3'},
  summary: {total: files.length, syntax_passed: files.filter(x => x.syntax_lua53 === 'passed').length,
    syntax_failed: files.filter(x => x.syntax_lua53 === 'failed').length,
    distinct_direct_global_calls: calls.size},
  limitations: [
    'Lua 5.3 syntax validation does not establish Lua 5.0 compatibility or equivalent behavior.',
    'Direct global calls include script functions and standard-library functions, not only native APIs.',
    'Calls through generated local aliases and dynamic dispatch are not resolved.',
    'Scripts failing syntax validation contribute no call inventory.'
  ],
  files, global_calls: [...calls.values()].sort((a,b) => b.count-a.count)
};
fs.mkdirSync(path.dirname(output), {recursive: true});
fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n', {flag: 'wx'});
console.log(JSON.stringify(report.summary));
