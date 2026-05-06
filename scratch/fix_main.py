import sys

def fix_main_py():
    with open('/home/givi/aegis/main.py', 'rb') as f:
        content = f.read()
    
    # We want to replace lines 266-286 (based on the previous cat -n output)
    # But since we just added lines, let's find the markers.
    
    lines = content.splitlines()
    
    # Find the line starting with '   265' (or similar)
    # Actually, I'll just look for the corrupted part.
    
    # The corrupted part starts after:
    # f"2️⃣ After sending, use: <code>/verify &lt;transaction_signature&gt;</code>\n\n"
    
    start_marker = b'transaction_signature'
    
    try:
        start_idx = -1
        for i, line in enumerate(lines):
            if start_marker in line:
                start_idx = i
                break
        
        if start_idx == -1:
            print("Could not find start marker")
            return

        # The end marker is the start of the next section
        end_marker = b'Degen Flow Objective Scoring'
        
        end_idx = -1
        for i in range(start_idx, len(lines)):
            if end_marker in lines[i]:
                end_idx = i
                break
        
        if end_idx == -1:
            print("Could not find end marker")
            return

        new_middle = [
            b'        f"<i>Note:</i> 60% burned \xf0\x9f\x94\xa5, 40% to treasury \xf0\x9f\x92\xb0"',
            b'    )',
            b'',
            b'async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):',
            b'    if not await _require_private_chat(update): return',
            b'    args = context.args',
            b'    if not args: await update.message.reply_text("Usage: /verify <tx_signature>"); return',
            b'    signature = args[0].strip(); user_id = update.effective_user.id',
            b'    msg = await update.message.reply_text("\xe2\x8c\x9b Verifying transaction on Solana...")',
            b'    result = await process_verification(user_id, signature)',
            b'    if not result["success"]:',
            b'        await msg.edit_text(f"\xe2\x9d\x8c Verification failed: {escape_html(result[\'error\'])}", parse_mode="HTML"); return',
            b'    expires = result["expires_at"].strftime("%Y-%m-%d")',
            b'    split_info = f"\\nSplit tx: <code>{escape_html(result[\'split_tx\'])}</code>" if result.get("split_tx") else ""',
            b'    await msg.edit_text(',
            b'        f"\xe2\x9d\x85 \u003cb\u003ePayment verified!\u003c/b\u003e\\nSubscription active until \u003cb\u003e{escape_html(expires)}\u003c/b\u003e.\\n"',
            b'        f"60% burned \xf0\x9f\x94\xa5, 40% to treasury \xf0\x9f\x92\xb0{split_info}", parse_mode="HTML"',
            b'    )',
            b'    if config["telegram"]["admin_user_id"]:',
            b'        try: await context.bot.send_message(config["telegram"]["admin_user_id"], f"\xf0\x9f\x92\xb0 New subscription: User {user_id} paid for 30 days.")',
            b'        except Exception: pass',
        ]
        
        new_content = lines[:start_idx+1] + new_middle + lines[end_idx:]
        
        with open('/home/givi/aegis/main.py', 'wb') as f:
            f.write(b'\n'.join(new_content))
            f.write(b'\n')
        
        print("Successfully fixed main.py")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fix_main_py()
