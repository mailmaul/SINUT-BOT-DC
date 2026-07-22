module.exports = {
  apps: [
    {
      name: 'sinut-bot-dc',
      script: 'bot.py',
      interpreter: './venv/bin/python',
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      // .env is loaded by bot.py via python-dotenv; creds.json must exist in cwd
    },
  ],
};
