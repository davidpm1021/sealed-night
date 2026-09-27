const { defineConfig } = require('@playwright/test');
module.exports=defineConfig({
  testDir:'tests/browser', timeout:30000, workers:1,
  use:{baseURL:'http://127.0.0.1:8765',trace:'retain-on-failure'},
  webServer:{command: process.env.BROWSER_SERVER_COMMAND || 'python -m uvicorn app.main:app --host 127.0.0.1 --port 8765',
    url:'http://127.0.0.1:8765/api/health',reuseExistingServer:false,
    env:{CARD_PROVIDER:'demo',DATA_DIR:'data/browser-tests'},timeout:30000}
});
