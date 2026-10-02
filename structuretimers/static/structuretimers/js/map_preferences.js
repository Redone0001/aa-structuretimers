/* Small, versioned preferences only: never timer data or campaign reservations. */
(function () {
    'use strict';
    window.StructureMapPreferences = {
        load(name) {
            try {const value=document.cookie.split('; ').find(row=>row.startsWith(name+'='));const data=value?JSON.parse(decodeURIComponent(value.slice(name.length+1))):{};return data&&data.version===1?data:{};} catch (_) {return {};}
        },
        save(name, data) {
            try {document.cookie=name+'='+encodeURIComponent(JSON.stringify({...data,version:1}))+'; Path=/; Max-Age=15552000; SameSite=Lax'+(location.protocol==='https:'?'; Secure':'');} catch (_) { /* Cookies may be disabled. */ }
        }
    };
})();
