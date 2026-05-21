# core/payment.py
"""
Payment verification and token splitting for Aegis subscriptions.
Handles Solana SPL token transfers: verifies payment, splits 60% burn / 40% treasury.
"""
import asyncio
import base58
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.instruction import Instruction

from core.solana_client import SolanaClient
from core.config import load_config

# ----------------------------------------------------------------------
# Hardcoded SPL Token constants (to avoid solders.token import issues)
# ----------------------------------------------------------------------
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

def get_associated_token_address(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive the associated token account address for an owner and mint."""
    seeds = [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)]
    return Pubkey.find_program_address(seeds, ASSOCIATED_TOKEN_PROGRAM_ID)[0]


class TransferCheckedParams:
    """Simplified params for transfer_checked instruction."""
    def __init__(self, program_id: Pubkey, source: Pubkey, mint: Pubkey, dest: Pubkey,
                 owner: Pubkey, amount: int, decimals: int):
        self.program_id = program_id
        self.source = source
        self.mint = mint
        self.dest = dest
        self.owner = owner
        self.amount = amount
        self.decimals = decimals


def transfer_checked(params: TransferCheckedParams) -> Instruction:
    """Build a TransferChecked instruction manually."""
    # TransferChecked instruction layout: instruction index 12
    data = bytearray()
    data.append(12)  # TransferChecked instruction index
    data.extend(params.amount.to_bytes(8, 'little'))
    data.append(params.decimals)
    
    keys = [
        {"pubkey": params.source, "is_signer": False, "is_writable": True},
        {"pubkey": params.mint, "is_signer": False, "is_writable": False},
        {"pubkey": params.dest, "is_signer": False, "is_writable": True},
        {"pubkey": params.owner, "is_signer": True, "is_writable": False},
    ]
    return Instruction(program_id=params.program_id, data=bytes(data), keys=keys)

# ----------------------------------------------------------------------

config = load_config()

TOKEN_MINT = Pubkey.from_string(config["solana"]["token_mint"])
BURN_ADDRESS = Pubkey.from_string("11111111111111111111111111111111")
TRANSACTION_TIMEOUT_MINUTES = 15

# Safe loading of payment receiver
_private_key = config["solana"]["payment_receiver_private_key"]
PAYMENT_RECEIVER = None
if _private_key:
    try:
        secret_bytes = base58.b58decode(_private_key)
        if len(secret_bytes) == 64:
            kp = Keypair.from_bytes(secret_bytes)
        else:
            kp = Keypair.from_seed(secret_bytes[:32])
        PAYMENT_RECEIVER = kp.pubkey()
    except Exception as e:
        print(f"⚠️  Warning: Invalid PAYMENT_RECEIVER_PRIVATE_KEY - payment features disabled. ({e})")

_treasury = config["solana"]["treasury_wallet"]
TREASURY_WALLET = None
if _treasury:
    try:
        TREASURY_WALLET = Pubkey.from_string(_treasury)
    except Exception as e:
        print(f"⚠️  Warning: Invalid TREASURY_WALLET - payment features disabled. ({e})")


class PaymentVerifier:
    def __init__(self, solana_client: SolanaClient, tier: str = "monthly"):
        self.client = solana_client
        self.payment_receiver = PAYMENT_RECEIVER
        self.token_mint = TOKEN_MINT
        self.tier = tier
        self._required_usd = config["subscription"]["tiers"].get(tier, config["subscription"]["tiers"]["monthly"])["price_usd"]

    async def get_token_price(self) -> float:
        price = await self.client.get_token_price(self.token_mint)
        if price is None:
            raise Exception("Could not fetch token price")
        return price

    async def required_tokens(self) -> float:
        price = await self.get_token_price()
        return self._required_usd / price

    def extract_token_transfer(self, tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not tx or "transaction" not in tx:
            return None

        message = tx["transaction"]["message"]
        instructions = message.get("instructions", [])
        meta = tx.get("meta", {})

        for ix in instructions:
            if "parsed" in ix and ix["parsed"]["type"] == "transfer":
                info = ix["parsed"]["info"]
                if (info.get("mint") == str(self.token_mint) and
                    info.get("destination") == str(self.payment_receiver)):

                    decimals = 9
                    pre_balances = meta.get("preTokenBalances", [])
                    for tb in pre_balances:
                        if tb.get("mint") == str(self.token_mint):
                            decimals = tb.get("decimals", 9)
                            break

                    return {
                        "amount": float(info["amount"]) / 10 ** decimals,
                        "amount_raw": int(info["amount"]),
                        "decimals": decimals,
                        "source_owner": info["sourceOwner"],
                        "source_token_account": info["source"],
                        "destination": info["destination"],
                    }
        return None

    async def verify_payment(self, signature: str) -> Dict[str, Any]:
        if not self.payment_receiver:
            return {"success": False, "error": "Payment receiver not configured."}

        tx = await self.client.get_transaction(signature)
        if not tx:
            return {"success": False, "error": "Transaction not found or not confirmed."}

        block_time = tx.get("blockTime")
        if not block_time:
            return {"success": False, "error": "Transaction not finalized."}
        tx_time = datetime.fromtimestamp(block_time, tz=timezone.utc)
        if datetime.now(timezone.utc) - tx_time > timedelta(minutes=TRANSACTION_TIMEOUT_MINUTES):
            return {"success": False, "error": "Transaction too old. Please make a new payment."}

        transfer = self.extract_token_transfer(tx)
        if not transfer:
            return {"success": False, "error": "No valid token transfer to our payment address."}

        required = await self.required_tokens()
        if transfer["amount"] < required * 0.98:
            return {"success": False, "error": f"Insufficient amount. Required ≈ {required:.2f} tokens."}

        return {
            "success": True,
            "amount": transfer["amount"],
            "amount_raw": transfer["amount_raw"],
            "decimals": transfer["decimals"],
            "sender": transfer["source_owner"],
            "source_token_account": transfer["source_token_account"],
            "signature": signature,
"usd_value": self._required_usd,
         }


class TokenSplitter:
    def __init__(self, solana_client: SolanaClient, payer_keypair: Keypair):
        self.client = solana_client
        self.payer = payer_keypair
        self.token_mint = TOKEN_MINT
        self.treasury = TREASURY_WALLET
        self.burn = BURN_ADDRESS

    async def split_and_send(
        self,
        source_token_account: Pubkey,
        amount_received: float,
        amount_raw: int,
        decimals: int,
    ) -> str:
        if not self.treasury:
            print("⚠️  Treasury wallet not set; cannot split tokens.")
            return ""

        treasury_ata = get_associated_token_address(self.treasury, self.token_mint)
        burn_ata = get_associated_token_address(self.burn, self.token_mint)

        burn_amount = int(amount_raw * 0.6)
        treasury_amount = amount_raw - burn_amount

        instructions = []

        if burn_amount > 0:
            params = TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=source_token_account,
                mint=self.token_mint,
                dest=burn_ata,
                owner=self.payer.pubkey(),
                amount=burn_amount,
                decimals=decimals,
            )
            instructions.append(transfer_checked(params))

        if treasury_amount > 0:
            params = TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=source_token_account,
                mint=self.token_mint,
                dest=treasury_ata,
                owner=self.payer.pubkey(),
                amount=treasury_amount,
                decimals=decimals,
            )
            instructions.append(transfer_checked(params))

        if not instructions:
            return ""

        recent_blockhash = await self.client.client.get_latest_blockhash(commitment="confirmed")
        msg = MessageV0.try_compile(
            payer=self.payer.pubkey(),
            instructions=instructions,
            address_lookup_table_accounts=[],
            recent_blockhash=recent_blockhash.value.blockhash,
        )
        tx = VersionedTransaction(msg, [self.payer])
        tx_bytes = bytes(tx)
        signature = await self.client.send_transaction(tx_bytes)
        return signature