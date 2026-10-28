/* 文件夹版打包：先在工作区外的临时目录完成 electron-builder 打包，再复制到 dist_client。
   部分编辑器/杀毒软件会索引并锁住 .asar 文件，导致 electron-builder 在项目目录内
   重命名或删除输出时失败；暂存目录不参与索引，最后只做一次普通复制。 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const builder = require('electron-builder');

const projectDir = path.resolve(__dirname, '..');
const stageDir = path.join(os.tmpdir(), 'dpc-electron-build');
const outDir = path.join(projectDir, 'dist_client');
const targetDir = path.join(outDir, 'win-unpacked');

// Node 的 fs.cpSync 在部分 Windows 环境下复制目录会静默失效，这里逐文件复制。
function copyTree(src, dst) {
  if (fs.statSync(src).isDirectory()) {
    fs.mkdirSync(dst, { recursive: true });
    for (const name of fs.readdirSync(src)) {
      copyTree(path.join(src, name), path.join(dst, name));
    }
    return;
  }
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.copyFileSync(src, dst);
}

(async () => {
  fs.rmSync(stageDir, { recursive: true, force: true });
  // 优先使用 node_modules 里已解压好的 Electron（离线可用），
  // 避免 electron-builder 每次都去 GitHub 下载（内网/弱网时常 ETIMEDOUT）。
  const localElectron = path.join(projectDir, 'node_modules', 'electron', 'dist');
  const electronDist = fs.existsSync(path.join(localElectron, 'electron.exe'))
    ? localElectron
    : undefined;
  if (electronDist) {
    console.log('使用本地 Electron：' + electronDist);
  } else {
    console.log('未找到本地 Electron，将联网下载');
  }

  await builder.build({
    dir: true,
    projectDir,
    config: {
      directories: { output: stageDir },
      ...(electronDist ? { electronDist } : {}),
    },
  });

  const built = path.join(stageDir, 'win-unpacked');
  const exeName = 'DrissionPageCatcher.exe';
  if (!fs.existsSync(path.join(built, exeName))) {
    throw new Error('未找到打包输出：' + built);
  }

  fs.rmSync(targetDir, { recursive: true, force: true });
  fs.mkdirSync(outDir, { recursive: true });
  copyTree(built, targetDir);

  const exe = path.join(targetDir, exeName);
  if (!fs.existsSync(exe)) {
    throw new Error('复制不完整：' + targetDir);
  }
  fs.rmSync(stageDir, { recursive: true, force: true });
  console.log('已生成文件夹版：' + targetDir);
  console.log('可执行文件：' + exe);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
