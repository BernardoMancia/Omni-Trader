const APP_DIR = process.env.OMNI_DIR || '/opt/omni-trader';
const PYTHON = `${APP_DIR}/venv/bin/python`;

module.exports = {
  apps: [
    {
      name: 'ingester',
      script: PYTHON,
      args: '-m services.data_ingester.main',
      cwd: APP_DIR,
      autorestart: true,
      max_memory_restart: '512M',
      watch: false,
    },
    {
      name: 'brain',
      script: PYTHON,
      args: '-m services.ai_brain.main',
      cwd: APP_DIR,
      autorestart: true,
      max_memory_restart: '512M',
      watch: false,
    },
    {
      name: 'router',
      script: PYTHON,
      args: '-m services.order_router.main',
      cwd: APP_DIR,
      autorestart: true,
      max_memory_restart: '700M',
      watch: false,
    },
    {
      name: 'notifier',
      script: PYTHON,
      args: '-m services.notifier.main',
      cwd: APP_DIR,
      autorestart: true,
      max_memory_restart: '400M',
      watch: false,
    },
    {
      name: 'watchdog',
      script: PYTHON,
      args: '-m services.watchdog.main',
      cwd: APP_DIR,
      autorestart: true,
      max_memory_restart: '200M',
      watch: false,
    },
  ],
};
