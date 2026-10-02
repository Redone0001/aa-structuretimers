const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let cookie='';const document={get cookie(){return cookie;},set cookie(value){cookie=value;}};
const context={window:{},document,location:{protocol:'https:'}};
vm.runInNewContext(fs.readFileSync(require.resolve('../../static/structuretimers/js/map_preferences.js'),'utf8'),context);
const prefs=context.window.StructureMapPreferences;
prefs.save('map',{region:'10000001',structures:false});assert.match(cookie,/SameSite=Lax; Secure$/);assert.match(cookie,/Max-Age=15552000/);assert.equal(prefs.load('map').region,'10000001');assert.equal(prefs.load('map').structures,false);
cookie='map=%broken';assert.equal(Object.keys(prefs.load('map')).length,0);
cookie='map='+encodeURIComponent(JSON.stringify({version:0,region:'wrong'}));assert.equal(Object.keys(prefs.load('map')).length,0);
console.log('Versioned cookies preserve values and reject malformed/obsolete preferences.');
