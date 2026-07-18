import sys, os, asyncio, json
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, REPO_ROOT)
from database import db
from datetime import datetime

async def main(user_id='local-dev-user', amount=10):
    wallet = await db.credit_wallets.find_one({'user_id': user_id})
    if not wallet:
        now = datetime.utcnow()
        wallet_doc = {
            'user_id': user_id,
            'balance': int(amount),
            'welcome_bonus_claimed_at': None,
            'last_daily_claim_at': None,
            'daily_claim_count': 0,
            'daily_claim_date': None,
            'created_at': now,
            'updated_at': now,
        }
        await db.credit_wallets.insert_one(wallet_doc)
        balance_before = 0
        balance_after = int(amount)
    else:
        balance_before = wallet.get('balance', 0)
        res = await db.credit_wallets.update_one({'user_id': user_id}, {'$inc': {'balance': int(amount)}, '$set': {'updated_at': datetime.utcnow()}})
        updated = await db.credit_wallets.find_one({'user_id': user_id})
        balance_after = updated.get('balance', balance_before)
    tx = {
        'user_id': user_id,
        'amount': int(amount),
        'type': 'admin_topup',
        'source': 'dev_topup',
        'balance_before': balance_before,
        'balance_after': balance_after,
        'created_at': datetime.utcnow(),
    }
    await db.credit_transactions.insert_one(tx)
    print(json.dumps({'user_id': user_id, 'balance_before': balance_before, 'balance_after': balance_after}, default=str))

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--user', default='local-dev-user')
    p.add_argument('--amount', type=int, default=10)
    args = p.parse_args()
    asyncio.run(main(args.user, args.amount))
