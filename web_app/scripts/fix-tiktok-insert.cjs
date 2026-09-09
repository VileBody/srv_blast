const fs = require('fs');
const file = 'frontend/src/pages/TikTokPostPage.tsx';
let source = fs.readFileSync(file, 'utf8');
source = source.replace('  const settleRef = useRef<number>();', '  const settleRef = useRef<number | undefined>(undefined);');
source = source.replace('\n+  const onPointerEnd', '\n  const onPointerEnd');
fs.writeFileSync(file, source);
