const fs = require('fs');
const file = 'frontend/src/pages/TikTokPostPage.tsx';
let source = fs.readFileSync(file, 'utf8');
source = source.replace('              poster={poster}\n', '              poster={poster ?? undefined}\n');
fs.writeFileSync(file, source);
