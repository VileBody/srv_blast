const fs = require('fs');
const file = 'frontend/src/pages/TikTokPostPage.tsx';
let source = fs.readFileSync(file, 'utf8');
source = source.replace('{ src?: string; poster?: string; value: number | null;', '{ src?: string | null; poster?: string | null; value: number | null;');
fs.writeFileSync(file, source);
