SELECT 'CREATE DATABASE yiqiao_app'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'yiqiao_app')\gexec
