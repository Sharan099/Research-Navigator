module.exports = async () => {
  // Stop the owned fixture server cleanly; Windows process-tree termination can be restricted.
  await fetch('http://127.0.0.1:8015/__test_shutdown', { method: 'POST' }).catch(() => {});
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try { await fetch('http://127.0.0.1:8015/api/health'); }
    catch { return; }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Disposable browser server did not stop after shutdown');
};
