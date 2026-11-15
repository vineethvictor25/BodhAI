import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from '@playwright/test'

const webDirectory=path.dirname(fileURLToPath(import.meta.url))
const projectRoot=path.resolve(webDirectory,'..')
const python=process.env.PYTHON || path.join(projectRoot,'.venv',
  process.platform==='win32'?'Scripts':'bin',
  process.platform==='win32'?'python.exe':'python')
const apiPort='8211'
const uiPort='3211'
const quote=(value:string)=>`"${value.replace(/"/g,'\\"')}"`

export default defineConfig({
  testDir:'./e2e',
  fullyParallel:false,
  reporter:'list',
  use:{
    baseURL:`http://127.0.0.1:${uiPort}`,
    browserName:'chromium',
    headless:true,
  },
  webServer:[
    {
      command:`${quote(python)} -m uvicorn e2e_server:app --app-dir ${quote(path.join(projectRoot,'tests'))} --host 127.0.0.1 --port ${apiPort} --log-level warning`,
      cwd:projectRoot,
      url:`http://127.0.0.1:${apiPort}/api/health`,
      reuseExistingServer:false,
      timeout:30000,
      env:{...process.env,QUERYDESK_E2E_API_PORT:apiPort,QUERYDESK_E2E_UI_PORT:uiPort},
    },
    {
      command:'npm run build && npm start',
      cwd:webDirectory,
      url:`http://127.0.0.1:${uiPort}`,
      reuseExistingServer:false,
      timeout:120000,
      env:{...process.env,UI_PORT:uiPort,QUERYDESK_API_URL:`http://127.0.0.1:${apiPort}`},
    },
  ],
})
