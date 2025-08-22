import os
import asyncio
import re
import json
import logging
import zipfile
import io
from datetime import datetime
from telethon import TelegramClient, events
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonViolence, InputReportReasonChildAbuse,
    InputReportReasonIllegalDrugs, InputReportReasonPornography, InputReportReasonPersonalDetails,
    InputReportReasonCopyright, InputReportReasonFake, InputReportReasonOther
)
from telethon.errors import FloodWaitError
import traceback

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- OWNER DETAILS & BOT CONFIGURATION ---
OWNER_ID = 8167904992  # Replace with your actual Telegram Chat ID
OWNER_USERNAME = "whatsapp_offcial"  # Replace with your actual Telegram Username

API_ID = 94575
API_HASH = 'a3406de8d171bb422bb6ddf3bbd800e2'
BOT_TOKEN = '7984874762:AAGc99zaI2M6CC0hFWIftxQ6B6ZknsjfKKw'

SESSION_FOLDER = 'sessions'
CHANNEL_DATA_FILE = 'channel_data.json'

# --- SPECIFIC ACCOUNT FOR CHECKING ---
CHECKING_PHONE_NUMBER = "+923117822922"

# --- MAPPING HUMAN-READABLE STRINGS TO TELEGRAM'S InputReportReason TYPES ---
REPORT_REASONS = {
    'Scam or spam': InputReportReasonSpam(),
    'Violence': InputReportReasonViolence(),
    'Child abuse': InputReportReasonChildAbuse(),
    'Illegal goods': InputReportReasonIllegalDrugs(),
    'Illegal adult content': InputReportReasonPornography(),
    'Personal data': InputReportReasonPersonalDetails(),
    'Terrorism': InputReportReasonViolence(),
    'Copyright': InputReportReasonCopyright(),
    'Other': InputReportReasonOther(),
    'I don’t like it': InputReportReasonOther(),
    'It’s not illegal, but must be taken down': InputReportReasonOther()
}

# --- MAPPING FOR SPECIFIC REPORT SUBTYPES ---
REPORT_SUBTYPES = {
    'Scam or spam': {
        'Phishing': InputReportReasonSpam(),
        'Impersonation': InputReportReasonFake(),
        'Fraudulent sales': InputReportReasonSpam(),
        'Spam': InputReportReasonSpam()
    },
    'Illegal goods': {
        'Weapons': InputReportReasonIllegalDrugs(),
        'Drugs': InputReportReasonIllegalDrugs(),
        'Fake documents': InputReportReasonFake(),
        'Counterfeit money': InputReportReasonFake(),
        'Other goods': InputReportReasonIllegalDrugs()
    },
    'Illegal adult content': {
        'Nudity': InputReportReasonPornography(),
        'Sexual abuse': InputReportReasonChildAbuse(),
        'Child sexual abuse material': InputReportReasonChildAbuse(),
        'Other adult content': InputReportReasonPornography()
    },
    'Personal data': {
        'Identity theft': InputReportReasonFake(),
        'Leaked phone number': InputReportReasonPersonalDetails(),
        'Leaked address': InputReportReasonPersonalDetails(),
        'Other personal data': InputReportReasonPersonalDetails()
    }
}

telethon_clients = {}
reporting_tasks = {}

# --- UTILITY FUNCTIONS ---
def init_files():
    if not os.path.exists(SESSION_FOLDER):
        os.makedirs(SESSION_FOLDER)
    if not os.path.exists(CHANNEL_DATA_FILE):
        with open(CHANNEL_DATA_FILE, 'w') as f:
            json.dump({}, f)

def load_channel_data():
    if not os.path.exists(CHANNEL_DATA_FILE):
        return {}
    with open(CHANNEL_DATA_FILE, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_channel_data(data):
    with open(CHANNEL_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def is_owner(user_id):
    return user_id == OWNER_ID

def mask_phone_number(phone_number):
    if len(phone_number) < 8:
        return phone_number
    return phone_number[:5] + '***' + phone_number[-5:]

def get_logged_in_accounts():
    accounts = []
    for user_folder in os.listdir(SESSION_FOLDER):
        user_path = os.path.join(SESSION_FOLDER, user_folder)
        if os.path.isdir(user_path) and user_folder.isdigit():
            for filename in os.listdir(user_path):
                if filename.endswith('.session'):
                    phone_number = os.path.splitext(filename)[0]
                    accounts.append((phone_number, int(user_folder)))
    return accounts

# --- BOT HANDLERS (TELEGRAM.EXT) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_owner(user_id):
        if update.message:
            await update.message.reply_text("You are not authorized to use this bot.")
        elif update.callback_query:
            await update.callback_query.edit_message_text("You are not authorized to use this bot.")
        return

    text = 'Hello Owner! Please choose an option:'
    keyboard = [
        [InlineKeyboardButton("Login 🔐", callback_data='login_start')],
        [InlineKeyboardButton("My Accounts 👤", callback_data='my_accounts')],
        [InlineKeyboardButton("Channel List 📢", callback_data='channel_list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await query.edit_message_text("You are not authorized to use this bot.")
        return
    
    if query.data == 'login_start':
        await query.edit_message_text(text="Please send your phone number with country code (e.g., +923001234567) to log in.")
        context.user_data['state'] = 'awaiting_phone_number'
    
    elif query.data == 'my_accounts':
        await manage_accounts(update, context)

    elif query.data.startswith('view_account_'):
        parts = query.data.split('_')
        if len(parts) != 4:
            await query.edit_message_text("❌ An error occurred. Please try again.")
            return
        
        phone_number, account_user_id = parts[2], parts[3]
        keyboard = [[
            InlineKeyboardButton("Delete Account 🗑️", callback_data=f'confirm_delete_{phone_number}_{account_user_id}'),
            InlineKeyboardButton("Back ↩️", callback_data='my_accounts')
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Options for account: {mask_phone_number(phone_number)}", reply_markup=reply_markup)
    
    elif query.data.startswith('confirm_delete_'):
        parts = query.data.split('_')
        phone_number, account_user_id = parts[2], parts[3]
        keyboard = [[
            InlineKeyboardButton("Confirm Delete ⚠️", callback_data=f'delete_account_{phone_number}_{account_user_id}'),
            InlineKeyboardButton("Cancel ❌", callback_data=f'view_account_{phone_number}_{account_user_id}')
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Are you sure you want to delete the session for {mask_phone_number(phone_number)}?", reply_markup=reply_markup)
    
    elif query.data.startswith('delete_account_'):
        parts = query.data.split('_')
        phone_number, account_user_id = parts[2], parts[3]
        await delete_account(update, context, phone_number, account_user_id)
        
    elif query.data == 'channel_list':
        await manage_channel_list(update, context)

    elif query.data == 'add_channel_start':
        await query.edit_message_text("Please send the channel link to add it to the list.")
        context.user_data['state'] = 'awaiting_channel_link'

    elif query.data.startswith('view_channel_'):
        channel_link = query.data.split('_', 2)[-1]
        await view_channel_details(update, context, channel_link)
        
    elif query.data.startswith('delete_channel_'):
        channel_link = query.data.split('_', 2)[-1]
        await delete_channel(update, context, channel_link)

    elif query.data.startswith('report_type_'):
        report_type_text = query.data.split('_', 2)[-1]
        context.user_data['report_type_text'] = report_type_text
        
        if report_type_text in REPORT_SUBTYPES:
            subtype_options = REPORT_SUBTYPES[report_type_text]
            keyboard_buttons = [[InlineKeyboardButton(text=opt, callback_data=f'report_subtype_{opt}')] for opt in subtype_options.keys()]
            reply_markup = InlineKeyboardMarkup(keyboard_buttons)
            await query.edit_message_text(f"Please choose a specific reason for '{report_type_text}':", reply_markup=reply_markup)
        else:
            await query.edit_message_text(f"You selected '{report_type_text}'. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'
            
    elif query.data.startswith('report_subtype_'):
        report_subtype_text = query.data.split('_', 2)[-1]
        context.user_data['report_type_text'] = report_subtype_text
        await query.edit_message_text(f"You selected '{report_subtype_text}'. Now, please provide a brief message and the number of times to report (e.g., 'Violent content 5').")
        context.user_data['state'] = 'awaiting_report_comment_and_count'

    elif query.data == 'start':
        await start(update, context)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    user_state = context.user_data.get('state')
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        return

    if user_state == 'awaiting_phone_number':
        phone_number = user_message
        try:
            user_session_folder = os.path.join(SESSION_FOLDER, str(user_id))
            if not os.path.exists(user_session_folder):
                os.makedirs(user_session_folder)
            
            session_path = os.path.join(user_session_folder, phone_number)
            
            if os.path.exists(session_path + '.session'):
                await update.message.reply_text("This account is already logged in. If you are having issues, please delete the old session file and try again.")
                context.user_data['state'] = None
                return

            client = TelegramClient(session_path, API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(phone_number)
                context.user_data['client'] = client
                context.user_data['phone_number'] = phone_number
                await update.message.reply_text("OTP has been sent to your number. Please enter the code.")
                context.user_data['state'] = 'awaiting_otp'
            else:
                await update.message.reply_text("This account is already logged in.")
                await client.disconnect()
                context.user_data['state'] = None
        except Exception as e:
            await update.message.reply_text(f"An error occurred: {e}. Please try again.")
            context.user_data['state'] = None

    elif user_state == 'awaiting_otp':
        otp = user_message
        client = context.user_data.get('client')
        phone_number = context.user_data.get('phone_number')

        if not client or not phone_number:
            await update.message.reply_text("Something went wrong. Please start the login process again.")
            context.user_data['state'] = None
            return

        try:
            await client.sign_in(code=otp)
            await update.message.reply_text("Successfully logged in! Your session file has been saved.")
            context.user_data['state'] = None
            context.user_data.pop('client', None)
            context.user_data.pop('phone_number', None)
            await initialize_all_clients(context.bot)
        except Exception as e:
            await update.message.reply_text(f"Invalid OTP. Please try again.")
    
    elif user_state == 'awaiting_channel_link':
        context.user_data['target_link'] = user_message
        keyboard_buttons = [[InlineKeyboardButton(text=key, callback_data=f'report_type_{key}')] for key in REPORT_REASONS.keys()]
        reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text("Please choose a report type:", reply_markup=reply_markup)
        context.user_data['state'] = 'awaiting_report_type_selection'

    elif user_state == 'awaiting_report_comment_and_count':
        try:
            parts = user_message.rsplit(' ', 1)
            report_message = parts[0].strip()
            report_count = int(parts[1].strip())
            
            target_link = context.user_data.get('target_link')
            report_type_text = context.user_data.get('report_type_text')

            channel_data = load_channel_data()
            channel_data[target_link] = {
                'report_type': report_type_text,
                'report_message': report_message,
                'report_count': report_count,
                'total_posts_reported': 0,
                'total_reports_sent': 0,
                'last_updated': datetime.now().isoformat()
            }
            save_channel_data(channel_data)
            
            await update.message.reply_text(f"✅ Channel '{target_link}' has been added to the list and will be automatically reported.")
            await initialize_all_clients(context.bot)
            
            context.user_data.clear()
            
        except (ValueError, IndexError):
            await update.message.reply_text("Please provide a comment and a number separated by a space (e.g., 'Violent content 5').")
            context.user_data['state'] = 'awaiting_report_comment_and_count'

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("You are not authorized to use this command.")
        return

    await update.message.reply_text("Creating backup, please wait...")
    
    try:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, dirs, files in os.walk(SESSION_FOLDER):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, os.path.dirname(SESSION_FOLDER))
                    zip_file.write(file_path, arcname)

            if os.path.exists(CHANNEL_DATA_FILE):
                zip_file.write(CHANNEL_DATA_FILE, os.path.basename(CHANNEL_DATA_FILE))

        buffer.seek(0)
        await update.message.reply_document(
            document=buffer,
            filename=f"bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            caption="✅ Backup created successfully! Includes session files and channel data."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to create backup. Error: {e}")

# --- CORE FUNCTIONALITY ---
async def manage_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    accounts = get_logged_in_accounts()

    if not accounts:
        await query.edit_message_text("No accounts are currently logged in.")
        return

    keyboard = []
    for phone_number, account_user_id in accounts:
        keyboard.append([
            InlineKeyboardButton(
                text=f"{mask_phone_number(phone_number)} (User: {account_user_id})",
                callback_data=f'view_account_{phone_number}_{account_user_id}'
            )
        ])
    keyboard.append([InlineKeyboardButton("Back ↩️", callback_data='start')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Please select an account to manage:", reply_markup=reply_markup)

async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE, phone_number: str, account_user_id: str):
    query = update.callback_query
    session_file_path = os.path.join(SESSION_FOLDER, str(account_user_id), f'{phone_number}.session')
    
    try:
        if os.path.exists(session_file_path):
            os.remove(session_file_path)
            journal_file_path = f"{session_file_path}-journal"
            if os.path.exists(journal_file_path):
                os.remove(journal_file_path)
            
            await query.edit_message_text(f"✅ Session file for {mask_phone_number(phone_number)} has been deleted.")
            await initialize_all_clients(context.bot)
        else:
            await query.edit_message_text(f"❌ Session file for {mask_phone_number(phone_number)} not found.")
    except Exception as e:
        await query.edit_message_text(f"❌ An error occurred while deleting the session file: {e}")

    await manage_accounts(update, context)

async def manage_channel_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    channel_data = load_channel_data()
    
    if not channel_data:
        keyboard = [[InlineKeyboardButton("Add Channel ➕", callback_data='add_channel_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("No channels have been added yet.", reply_markup=reply_markup)
        return

    keyboard = []
    for link in channel_data.keys():
        channel_name = link.split('/')[-1] if 't.me' in link else link
        keyboard.append([
            InlineKeyboardButton(f"{channel_name}", callback_data=f'view_channel_{link}')
        ])
    
    keyboard.append([InlineKeyboardButton("Add Channel ➕", callback_data='add_channel_start')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Your current channel list:", reply_markup=reply_markup)

async def view_channel_details(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_link: str):
    query = update.callback_query
    channel_data = load_channel_data()
    
    if channel_link not in channel_data:
        await query.edit_message_text("❌ Channel not found in list.")
        return

    data = channel_data[channel_link]
    
    message = (
        f"**Channel Details:**\n"
        f"**Link:** {channel_link}\n"
        f"**Report Type:** {data.get('report_type', 'N/A')}\n"
        f"**Report Message:** {data.get('report_message', 'N/A')}\n"
        f"**Reports per post:** {data.get('report_count', 0)}\n"
        f"**Total Posts Reported:** {data.get('total_posts_reported', 0)}\n"
        f"**Total Reports Sent:** {data.get('total_reports_sent', 0)}\n"
    )
    
    keyboard = [[InlineKeyboardButton("Delete Channel 🗑️", callback_data=f'delete_channel_{channel_link}')],
                [InlineKeyboardButton("Back ↩️", callback_data='channel_list')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def delete_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_link: str):
    query = update.callback_query
    channel_data = load_channel_data()
    
    if channel_link in channel_data:
        del channel_data[channel_link]
        save_channel_data(channel_data)
        await query.edit_message_text(f"✅ Channel '{channel_link}' has been deleted from the list.")
        await initialize_all_clients(context.bot)
    else:
        await query.edit_message_text(f"❌ Channel '{channel_link}' not found in the list.")

    await manage_channel_list(update, context)

# --- AUTOMATIC REPORTING LOGIC ---
async def handle_new_post(event, bot, channel_link):
    channel_data = load_channel_data()
    if channel_link not in channel_data:
        return

    data = channel_data[channel_link]
    
    report_type = data['report_type']
    report_message = data['report_message']
    report_count = data['report_count']

    await bot.send_message(
        chat_id=OWNER_ID, 
        text=f"**ایک نئی پوسٹ آ گئی ہے!** 📢\n"
             f"**چینل:** {channel_link}\n"
             f"**رپورٹ کی قسم:** {report_type}\n"
             f"آٹومیٹک رپورٹنگ شروع ہو گئی ہے۔..",
        parse_mode=constants.ParseMode.MARKDOWN
    )

    await report_message_from_all_accounts(bot, channel_link, event.id, report_type, report_message, report_count)
    
    data['total_posts_reported'] += 1
    data['total_reports_sent'] += report_count
    save_channel_data(channel_data)
    
    await bot.send_message(
        chat_id=OWNER_ID, 
        text=f"✅ **رپورٹنگ مکمل!**\n"
             f"**چینل:** {channel_link}\n"
             f"**پوسٹ ID:** `{event.id}`\n"
             f"**کل رپورٹس بھیجی گئیں:** {report_count * (len(telethon_clients.keys()) - 1)}",
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def report_message_from_all_accounts(bot, channel_link, message_id, report_type, report_message, report_count):
    accounts_to_use = [phone for phone in telethon_clients.keys() if phone != CHECKING_PHONE_NUMBER]
    
    report_reason_obj = None
    if report_type in REPORT_REASONS:
        report_reason_obj = REPORT_REASONS[report_type]
    else:
        for main_type, subtypes in REPORT_SUBTYPES.items():
            if report_type in subtypes:
                report_reason_obj = subtypes[report_type]
                break
    
    if report_reason_obj is None:
        await bot.send_message(
            chat_id=OWNER_ID,
            text=f"❌ Invalid report type for auto-reporting: {report_type}"
        )
        return

    tasks = []
    for phone_number in accounts_to_use:
        task = asyncio.create_task(send_single_report_task(bot, phone_number, channel_link, message_id, report_reason_obj, report_message, report_count))
        tasks.append(task)
        
    await asyncio.gather(*tasks, return_exceptions=True)

async def send_single_report_task(bot, phone_number, channel_link, message_id, report_reason_obj, report_message, report_count):
    client = telethon_clients.get(phone_number)
    if not client:
        return

    try:
        entity = await client.get_entity(channel_link)
        
        for i in range(report_count):
            await client(ReportRequest(
                peer=entity, 
                id=[message_id], 
                reason=report_reason_obj, 
                message=report_message
            ))
            logging.info(f"Report {i+1}/{report_count} sent from {phone_number} for post {message_id} in {channel_link}.")
            await asyncio.sleep(2)
        
        logging.info(f"✅ Reports sent successfully from {phone_number} for post {message_id} in {channel_link}.")
    except FloodWaitError as e:
        await bot.send_message(
            chat_id=OWNER_ID,
            text=f"❌ FloodWaitError for account {mask_phone_number(phone_number)}: {e.seconds} seconds. Skipping this post."
        )
        logging.warning(f"FloodWaitError for {phone_number}: {e.seconds}s")
    except Exception as e:
        await bot.send_message(
            chat_id=OWNER_ID,
            text=f"❌ Report failed from {mask_phone_number(phone_number)} for post {message_id} in {channel_link}. Reason: {e}"
        )
        logging.error(f"Error for account {phone_number}: {traceback.format_exc()}")
        
async def initialize_all_clients(app):
    bot = app.bot
    global telethon_clients
    
    for client in list(telethon_clients.values()):
        if client.is_connected():
            try:
                await client.disconnect()
            except Exception as e:
                logging.error(f"Failed to disconnect client: {e}")
    
    telethon_clients.clear()
    
    accounts = get_logged_in_accounts()
    checking_client = None
    
    for phone_number, user_id in accounts:
        session_path = os.path.join(SESSION_FOLDER, str(user_id), phone_number)
        client = TelegramClient(session_path, API_ID, API_HASH)
        telethon_clients[phone_number] = client
        if phone_number == CHECKING_PHONE_NUMBER:
            checking_client = client
    
    if not checking_client:
        await bot.send_message(
            chat_id=OWNER_ID,
            text=f"❌ Warning: The checking account {CHECKING_PHONE_NUMBER} is not logged in. Automatic reporting will not start."
        )
        logging.error(f"Checking account {CHECKING_PHONE_NUMBER} not found. Exiting.")
        return

    try:
        await checking_client.start()
    except Exception as e:
        logging.error(f"Failed to start checking client: {e}")
        return
    
    for channel_link in load_channel_data().keys():
        try:
            entity = await checking_client.get_entity(channel_link)
            
            # Use a partial to pass the arguments correctly to the handler
            checking_client.add_event_handler(
                lambda event, cl=channel_link: handle_new_post(event, bot, cl),
                events.NewMessage(chats=entity, incoming=True)
            )
            
        except Exception as e:
            logging.error(f"Failed to add event handler for channel {channel_link}: {e}")
            
    other_clients = [client for phone, client in telethon_clients.items() if phone != CHECKING_PHONE_NUMBER]
    await asyncio.gather(*[client.start() for client in other_clients], return_exceptions=True)
    logging.info(f"Started monitoring {len(load_channel_data())} channels with the dedicated account.")

def main() -> None:
    init_files()
    application = Application.builder().token(BOT_TOKEN).post_init(initialize_all_clients).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
