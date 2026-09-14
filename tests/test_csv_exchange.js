const assert=require('assert');
function csvEscape(v){const s=String(v??'');return /[",\n\r]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s}
function toExchangeText(value){let s=String(value??'');if(s.startsWith('$'))s=s.slice(1);return s.replace(/<\\n>/gi,'\n')}
function fromExchangeText(value,sourceTemplate){let s=String(value??'').replace(/\r\n?/g,'\n');s=s.replace(/\n/g,'<\\n>');const src=String(sourceTemplate??'');if(src.startsWith('$')&&!s.startsWith('$'))s='$'+s;return s}
assert.equal(toExchangeText('$Mỉm cười'),'Mỉm cười');
assert.equal(fromExchangeText('微笑','$Mỉm cười'),'$微笑');
assert.equal(toExchangeText('$第一行<\\n>第二行'),'第一行\n第二行');
assert.equal(fromExchangeText('第一行\n第二行','$第一行<\\n>第二行'),'$第一行<\\n>第二行');
assert.equal(csvEscape('第一行\n第二行'),'"第一行\n第二行"');
console.log('CSV exchange tests passed');
