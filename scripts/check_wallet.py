import sys, os, asyncio, json
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, REPO_ROOT)
from database import db

async def main(user_id='local-dev-user'):
    wallet = await db.credit_wallets.find_one({'user_id': user_id})
    txs = await db.credit_transactions.find({'user_id': user_id}).sort('created_at', -1).limit(20).to_list(length=20)
    print(json.dumps({'user_id': user_id, 'wallet': wallet, 'recent_transactions': txs}, default=str, indent=2))

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--user', default='local-dev-user')
    args = p.parse_args()
    asyncio.run(main(args.user))
