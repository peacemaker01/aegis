import asyncio
from aegis_solana.rpc_client import SolanaRPCClient
from aegis_solana.session import _get_deployer_address

async def main():
    rpc_client = SolanaRPCClient(
        endpoint="https://mainnet.helius-rpc.com",
        api_key="9b988f0c-c793-4a81-869a-dd95620886bf"
    )
    res = await _get_deployer_address("DnHcttayVM8AzfPuf2iaRD8hJDFqFxFD2J6yMTRZ3mYw", rpc_client)
    print(f"Deployer extracted: {res}")

asyncio.run(main())
