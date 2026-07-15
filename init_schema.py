import asyncio
import asyncpg
from config import DATABASE_URL

async def run():
    pool = await asyncpg.create_pool(DATABASE_URL)
    with open('postgres_schema.sql') as f:
        schema = f.read()
    await pool.execute(schema)
    print('Schema initialized successfully.')

if __name__ == '__main__':
    asyncio.run(run())
