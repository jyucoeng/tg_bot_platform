#!/usr/bin/env python3
import os
import logging
import asyncio
import random
from datetime import datetime
from functools import partial
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.error import BadRequest
from dotenv import load_dotenv
load_dotenv()

# ================== 数据库模块 ==================
import database as db

# ================== 配置 ==================
ADMIN_CHANNEL = os.environ.get("ADMIN_CHANNEL")      # 宿主通知群/频道（可选）
MANAGER_TOKEN = os.environ.get("MANAGER_TOKEN")      # 管理机器人 Token（必须）

bots_data = {}
msg_map = {}
pending_verifications = {}  # 待验证用户（内存临时数据）
running_apps = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ================== 工具函数 ==================
def load_bots():
    """从数据库加载 Bot 配置"""
    global bots_data
    all_bots = db.get_all_bots()
    
    bots_data = {}
    for bot_username, bot_info in all_bots.items():
        owner_id = str(bot_info['owner'])
        if owner_id not in bots_data:
            bots_data[owner_id] = {"bots": []}
        bots_data[owner_id]["bots"].append({
            "bot_username": bot_username,
            "token": bot_info['token'],
            "welcome_msg": bot_info.get('welcome_msg', ''),
            "mode": bot_info.get('mode', 'direct'),
            "forum_group_id": bot_info.get('forum_group_id'),
            "verification_type": bot_info.get('verification_type', 'simple')
        })


    
    logger.info(f"✅ 从数据库加载了 {len(all_bots)} 个 Bot")
    return bots_data

def save_bots():
    """保存 Bot 配置到数据库"""
    pass

def load_map():
    """从数据库加载消息映射"""
    global msg_map
    msg_map = {}
    
    # 从数据库加载所有机器人的映射
    all_bots = db.get_all_bots()
    for bot_username in all_bots.keys():
        ensure_bot_map(bot_username)
        
        # 加载各种类型的映射
        msg_map[bot_username]["direct"] = db.get_all_mappings(bot_username, "direct")
        
        # 加载 topic 映射（需要转换为 int）
        topic_mappings = db.get_all_mappings(bot_username, "topic")
        msg_map[bot_username]["topics"] = {k: int(v) for k, v in topic_mappings.items() if v.isdigit()}
        
        msg_map[bot_username]["user_to_forward"] = db.get_all_mappings(bot_username, "user_forward")
        msg_map[bot_username]["forward_to_user"] = db.get_all_mappings(bot_username, "forward_user")
        msg_map[bot_username]["owner_to_user"] = db.get_all_mappings(bot_username, "owner_user")
    
    logger.info(f"✅ 从数据库加载了 {len(msg_map)} 个 Bot 的消息映射")

def save_map():
    """保存消息映射到数据库"""
    pass

def trigger_backup(silent=False):
    """触发自动备份（异步执行，不阻塞主进程）
    
    Args:
        silent: 是否静默备份（不推送通知）
    """
    import subprocess
    # 支持环境变量配置备份脚本路径
    backup_script = os.environ.get('BACKUP_SCRIPT_PATH',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backup.sh'))    
    # 检查备份脚本是否存在
    if not os.path.exists(backup_script):
        logger.info("⏭️  备份脚本不存在，跳过自动备份")
        return
    
    try:
        # 构建环境变量
        env = os.environ.copy()
        if silent:
            env["SILENT_BACKUP"] = "1"  # 传递静默标志
        
        # 异步执行备份脚本（不等待完成）
        subprocess.Popen(
            ["/bin/bash", backup_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # 脱离主进程
            env=env
        )
        logger.info(f"🔄 已触发{'静默' if silent else ''}备份（后台执行）")
    except Exception as e:
        logger.error(f"❌ 触发备份失败: {e}")

# 使用数据库的验证用户管理
def is_verified(bot_username: str, user_id: int) -> bool:
    """检查用户是否已验证"""
    return db.is_verified(bot_username, user_id)

def add_verified_user(bot_username: str, user_id: int, user_name: str = "", user_username: str = ""):
    """添加已验证用户"""
    db.add_verified_user(bot_username, user_id, user_name, user_username)

def remove_verified_user(bot_username: str, user_id: int):
    """取消用户验证"""
    return db.remove_verified_user(bot_username, user_id)

def generate_captcha() -> dict:
    """生成复杂验证码（多种类型）- 完全免费"""
    captcha_type = random.choice(['math', 'sequence', 'chinese', 'logic', 'time'])
    
    if captcha_type == 'math':
        # 数学运算验证码（升级版：支持多步运算）
        style = random.choice(['simple', 'complex'])
        if style == 'simple':
            operators = ['+', '-', '*']
            op = random.choice(operators)
            if op == '+':
                a, b = random.randint(10, 99), random.randint(10, 99)
                answer = str(a + b)
                question = f"{a} + {b} = ?"
            elif op == '-':
                a, b = random.randint(50, 99), random.randint(10, 49)
                answer = str(a - b)
                question = f"{a} - {b} = ?"
            else:  # *
                a, b = random.randint(2, 12), random.randint(2, 12)
                answer = str(a * b)
                question = f"{a} × {b} = ?"
        else:  # complex - 两步运算
            a, b, c = random.randint(5, 20), random.randint(2, 10), random.randint(2, 10)
            ops = random.choice([
                ('+', '*'),  # 先乘后加
                ('-', '+'),  # 混合
            ])
            if ops == ('+', '*'):
                answer = str(a + b * c)
                question = f"{a} + {b} × {c} = ?"
            else:
                answer = str(a - b + c)
                question = f"{a} - {b} + {c} = ?"
        
        return {
            'type': 'math',
            'question': question,
            'answer': answer
        }
    
    elif captcha_type == 'sequence':
        # 数字序列验证码（找规律）
        patterns = [
            # 等差数列
            lambda: {
                'nums': (start := random.randint(1, 10), d := random.randint(2, 5)),
                'seq': [start + i*d for i in range(4)],
                'answer': str(start + 4*d)
            },
            # 等比数列
            lambda: {
                'nums': (start := random.randint(2, 5), r := random.randint(2, 3)),
                'seq': [start * (r**i) for i in range(4)],
                'answer': str(start * (r**4))
            },
            # 平方数列
            lambda: {
                'nums': (start := random.randint(1, 5),),
                'seq': [(start + i)**2 for i in range(4)],
                'answer': str((start + 4)**2)
            }
        ]
        pattern = random.choice(patterns)()
        question = f"找规律填空：{', '.join(map(str, pattern['seq']))}, ?"
        
        return {
            'type': 'sequence',
            'question': question,
            'answer': pattern['answer']
        }
    
    elif captcha_type == 'chinese':
        # 中文数字验证码（防机器人效果极佳）
        chinese_nums = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']
        num = random.randint(10, 99)
        # 修复：先判断是否为10-19，避免重复赋值
        if num // 10 == 1:
            chinese_form = '十' + (chinese_nums[num % 10] if num % 10 != 0 else '')
        else:
            chinese_form = chinese_nums[num // 10] + '十' + (chinese_nums[num % 10] if num % 10 != 0 else '')
        
        return {
            'type': 'chinese',
            'question': f"请将中文数字转为阿拉伯数字",
            'answer': str(num),
            'display': chinese_form
        }
    
    elif captcha_type == 'logic':
        # 逻辑推理验证码
        logic_puzzles = [
            # 年龄问题
            {
                'question': lambda: (
                    age := random.randint(8, 15),
                    f"小明今年{age}岁，5年后他多少岁？"
                )[1],
                'answer': lambda age=random.randint(8, 15): str(age + 5)
            },
            # 时间问题
            {
                'question': lambda: (
                    hours := random.randint(2, 5),
                    f"现在是上午10点，{hours}小时后是几点？（只填数字，如：15）"
                )[1],
                'answer': lambda hours=random.randint(2, 5): str(10 + hours)
            },
            # 数量问题
            {
                'question': lambda: (
                    apples := random.randint(5, 12),
                    eat := random.randint(2, 4),
                    f"小红有{apples}个苹果，吃了{eat}个，还剩几个？"
                )[1],
                'answer': lambda apples=random.randint(5, 12), eat=random.randint(2, 4): str(apples - eat)
            }
        ]
        
        # 简化逻辑题
        scenarios = [
            (lambda: random.randint(8, 15), lambda age: f"小明今年{age}岁，5年后他多少岁？", lambda age: str(age + 5)),
            (lambda: random.randint(2, 5), lambda h: f"现在是10点，{h}小时后几点？", lambda h: str(10 + h)),
            (lambda: (random.randint(8, 15), random.randint(2, 5)), lambda x: f"有{x[0]}个，吃{x[1]}个，剩几个？", lambda x: str(x[0] - x[1])),
        ]
        
        scenario = random.choice(scenarios)
        value = scenario[0]()
        question = scenario[1](value)
        answer = scenario[2](value)
        
        return {
            'type': 'logic',
            'question': question,
            'answer': answer
        }
    
    else:  # time
        # 时间识别验证码 - 修复版：明确上午/下午/晚上
        minute = random.choice([0, 15, 30, 45])
        
        # 根据时间段选择合适的小时
        period = random.choice(['上午', '下午', '晚上'])
        
        if period == '上午':
            # 上午：6点-11点 (06:00-11:59)
            hour_12 = random.randint(6, 11)
            hour_24 = hour_12
        elif period == '下午':
            # 下午：12点-5点 (12:00-17:59)
            hour_12 = random.choice([12, 1, 2, 3, 4, 5])
            hour_24 = hour_12 if hour_12 == 12 else hour_12 + 12
        else:  # 晚上
            # 晚上：6点-11点 (18:00-23:59)
            hour_12 = random.randint(6, 11)
            hour_24 = hour_12 + 12
        
        # 中文数字（1-12对应的中文）
        hour_cn_map = {
            1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六',
            7: '七', 8: '八', 9: '九', 10: '十', 11: '十一', 12: '十二'
        }
        hour_cn = hour_cn_map[hour_12]
        
        # 中文表达时间
        time_str = f"{period}{hour_cn}点"
        if minute == 15:
            time_str += "一刻"
        elif minute == 30:
            time_str += "半"
        elif minute == 45:
            time_str += "三刻"
        
        answer = f"{hour_24:02d}:{minute:02d}"
        
        return {
            'type': 'time',
            'question': f"请用24小时制表示（格式：HH:MM）",
            'answer': answer,
            'display': time_str
        }

# 使用数据库的黑名单管理
def is_blacklisted(bot_username: str, user_id: int) -> bool:
    """检查用户是否在黑名单中"""
    return db.is_blacklisted(bot_username, user_id)

def add_to_blacklist(bot_username: str, user_id: int, reason: str = ""):
    """添加用户到黑名单"""
    db.add_to_blacklist(bot_username, user_id, reason)
    return True

def remove_from_blacklist(bot_username: str, user_id: int):
    """从黑名单移除用户"""
    return db.remove_from_blacklist(bot_username, user_id)

def ensure_bot_map(bot_username: str):
    """保证 msg_map 结构存在"""
    if bot_username not in msg_map or not isinstance(msg_map[bot_username], dict):
        msg_map[bot_username] = {}
    # 直连：主人的被转发消息 msg_id -> 用户ID
    msg_map[bot_username].setdefault("direct", {})
    # 话题：用户ID(str) -> topic_id(int)
    msg_map[bot_username].setdefault("topics", {})
    # 用户消息ID -> 转发后的消息ID (用于编辑消息)
    msg_map[bot_username].setdefault("user_to_forward", {})
    # 转发消息ID -> 用户消息ID (用于反向查找)
    msg_map[bot_username].setdefault("forward_to_user", {})
    # 主人消息ID -> 发送给用户的消息ID (用于编辑主人发送的消息)
    msg_map[bot_username].setdefault("owner_to_user", {})

async def reply_and_auto_delete(message, text, delay=5, **kwargs):
    try:
        sent = await message.reply_text(text, **kwargs)
        await asyncio.sleep(delay)
        await sent.delete()
    except Exception:
        pass

async def send_and_auto_delete(context, chat_id, text, delay=5, **kwargs):
    """发送消息并自动删除(不使用reply)"""
    try:
        sent = await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        await asyncio.sleep(delay)
        await sent.delete()
    except Exception:
        pass

async def send_admin_log(text: str):
    if not ADMIN_CHANNEL:
        return
    try:
        app = running_apps.get("__manager__")
        if app:
            await app.bot.send_message(chat_id=ADMIN_CHANNEL, text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"宿主通知失败: {e}")

def get_bot_cfg(owner_id, bot_username: str):
    """从 bots_data 中找到某个 owner 的某个子机器人配置"""
    owner_id = str(owner_id)
    info = bots_data.get(owner_id, {})
    for b in info.get("bots", []):
        if b.get("bot_username") == bot_username:
            return b
    return None

# 系统默认欢迎语模板
DEFAULT_WELCOME_MSG = (
    "👋 欢迎回来！\n\n"
    "--------------------------\n"
    "✨ 核心功能\n"
    "* 多机器人接入：只需提供 Token，即可快速启用。\n\n"
    "* 两种模式：\n"
    "  ▸ 私聊模式 —— 用户消息直接转发到bot。\n"
    "  ▸ 话题模式 —— 每个用户自动建立独立话题，消息更清晰。\n\n"
    "* 智能映射：自动维护消息与话题的对应关系。\n"
    "---------------------------\n"
    "- 客服bot托管中心 @tg_multis_bot \n"
    "---------------------------\n\n"
    "请直接输入消息，主人收到就会回复你"
)

def get_welcome_message(bot_username: str) -> str:
    """
    获取欢迎语（按优先级）
    1. 用户自定义欢迎语（bot配置中的welcome_msg）
    2. 管理员全局欢迎语（global_settings表）
    3. 系统默认欢迎语（DEFAULT_WELCOME_MSG常量）
    
    Args:
        bot_username: 机器人用户名
    
    Returns:
        欢迎语文本
    """
    # 优先级1：用户自定义欢迎语
    bot_info = db.get_bot(bot_username)
    if bot_info and bot_info.get('welcome_msg'):
        return bot_info['welcome_msg']
    
    # 优先级2：管理员全局欢迎语
    global_welcome = db.get_global_welcome()
    if global_welcome:
        return global_welcome
    
    # 优先级3：系统默认欢迎语
    return DEFAULT_WELCOME_MSG

# ================== 宿主机 /start 菜单 ==================
def is_admin(user_id: int) -> bool:
    """检查用户是否为管理员"""
    return str(user_id) == str(ADMIN_CHANNEL)

def manager_main_menu(user_id: int):
    """生成主菜单（普通用户和管理员有不同选项）"""
    keyboard = [
        [InlineKeyboardButton("➕ 添加机器人", callback_data="addbot")],
        [InlineKeyboardButton("🤖 我的机器人", callback_data="mybots")]
    ]
    
    # 管理员专属菜单
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("📝 全局欢迎语", callback_data="admin_global_welcome")])
        keyboard.append([InlineKeyboardButton("👥 用户清单", callback_data="admin_users")])
        keyboard.append([InlineKeyboardButton("📢 广播通知", callback_data="admin_broadcast")])
        keyboard.append([InlineKeyboardButton("🗑️ 清理失效Bot", callback_data="admin_clean_invalid")])
    
    return InlineKeyboardMarkup(keyboard)

async def manager_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        user_id = update.message.from_user.id
        await update.message.reply_text("📣 欢迎使用客服机器人管理面板\n👇 请选择操作：", reply_markup=manager_main_menu(user_id))
    elif update.callback_query:
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()
        await query.message.edit_text("📣 欢迎使用客服机器人管理面板\n👇 请选择操作：", reply_markup=manager_main_menu(user_id))

# ================== 子机器人 /start ==================
async def subbot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """子机器人的 /start 命令，发送验证码或欢迎消息"""
    user_id = update.message.from_user.id
    bot_username = context.bot.username
    
    # 如果用户已验证，显示欢迎信息
    if is_verified(bot_username, user_id):
        # 使用优先级欢迎语：用户自定义 > 管理员全局 > 系统默认
        welcome_msg = get_welcome_message(bot_username)
        await update.message.reply_text(welcome_msg)
    else:
        # 获取 Bot 配置以确定验证类型（直接从数据库读取，确保是最新的）
        bot_info = db.get_bot(bot_username)
        verification_type = bot_info.get('verification_type', 'simple') if bot_info else 'simple'
        
        if verification_type == 'cf':

            # CF Turnstile 验证流程
            user_name = update.message.from_user.full_name or "匿名用户"
            user_username = update.message.from_user.username or ""
            
            # 发送验证消息（带按钮）
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            
            message_text = (
                "🔐 <b>Cloudflare 验证</b>\n\n"
                "欢迎使用本机器人！\n"
                "为确保安全，首次使用需要完成验证。\n\n"
                "📝 请点击下方按钮完成验证：\n\n"
                "⏰ 验证链接有效期：<b>5分钟</b>\n"
                "🔗 验证完成后，返回此处继续使用"
            )
            
            # 先发送消息，获取 message_id
            sent_message = await update.message.reply_text(
                message_text,
                parse_mode="HTML"
            )
            
            # 创建验证令牌（保存 message_id）
            token = db.create_verification_token(bot_username, user_id, user_name, user_username, sent_message.message_id)
            
            if not token:
                await update.message.reply_text("❌ 生成验证链接失败，请稍后重试或联系管理员")
                return
            
            # 构建验证 URL
            verify_url = f"{os.environ.get('VERIFY_SERVER_URL', 'http://localhost:5000')}/verify/{token}"
            
            # 编辑消息添加按钮
            keyboard = [
                [InlineKeyboardButton("🔗 点击验证", url=verify_url)]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await sent_message.edit_text(
                message_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif verification_type == 'manual':
            # 人工验证流程：提交申请，等待管理员审核
            bot_info = db.get_bot(bot_username)
            owner_id = bot_info['owner'] if bot_info else 0
            await request_manual_verification(context, update.message, bot_username, owner_id)
        elif verification_type == 'deny':
            # 自动拒绝：拒绝所有新用户
            await update.message.reply_text(
                "⛔ 该机器人暂不接受新用户，已拒绝你的访问。\n\n"
                "如果你是已有用户，请联系管理员。",
                parse_mode="HTML"
            )
        elif verification_type == 'none':
            # 取消验证：所有用户直接放行
            welcome_msg = get_welcome_message(bot_username)
            await update.message.reply_text(welcome_msg)
        else:
            # 简单验证码流程（原有逻辑）
            captcha_data = generate_captcha()
            # 💾 保存到数据库（持久化）
            db.add_pending_verification(bot_username, user_id, captcha_data['answer'])
            # 内存中也保留（用于快速访问）
            verification_key = f"{bot_username}_{user_id}"
            pending_verifications[verification_key] = captcha_data['answer']
            
            # 根据验证码类型构建消息
            captcha_type = captcha_data['type']
            
            if captcha_type == 'math':
                message_text = (
                    f"🔐 数学验证\n\n"
                    f"欢迎使用本机器人！\n"
                    f"为防止滥用，首次使用需要验证。\n\n"
                    f"📝 请计算：<b>{captcha_data['question']}</b>\n\n"
                    f"💡 提示：请输入计算结果（纯数字）"
                )
            elif captcha_type == 'sequence':
                message_text = (
                    f"🔐 逻辑验证\n\n"
                    f"欢迎使用本机器人！\n"
                    f"为防止滥用，首次使用需要验证。\n\n"
                    f"📝 {captcha_data['question']}\n\n"
                    f"💡 提示：观察规律，填入下一个数字"
                )
            elif captcha_type == 'chinese':
                message_text = (
                    f"🔐 中文数字验证\n\n"
                    f"欢迎使用本机器人！\n"
                    f"为防止滥用，首次使用需要验证。\n\n"
                    f"📝 中文数字：<b>{captcha_data['display']}</b>\n\n"
                    f"💡 {captcha_data['question']}"
                )
            elif captcha_type == 'logic':
                message_text = (
                    f"🔐 智力验证\n\n"
                    f"欢迎使用本机器人！\n"
                    f"为防止滥用，首次使用需要验证。\n\n"
                    f"📝 {captcha_data['question']}\n\n"
                    f"💡 提示：简单的逻辑题，输入数字答案"
                )
            elif captcha_type == 'time':
                message_text = (
                    f"🔐 时间验证\n\n"
                    f"欢迎使用本机器人！\n"
                    f"为防止滥用，首次使用需要验证。\n\n"
                    f"📝 时间：<b>{captcha_data['display']}</b>\n\n"
                    f"💡 {captcha_data['question']}"
                )
            else:
                message_text = (
                    f"🔐 验证\n\n"
                    f"欢迎使用本机器人！\n"
                    f"为防止滥用，首次使用需要验证。\n\n"
                    f"📝 {captcha_data['question']}\n\n"
                    f"💡 提示：请输入答案"
                )
            
            await update.message.reply_text(message_text, parse_mode="HTML")

def get_bot_owner(bot_username: str) -> int:
    """获取 Bot 的 owner ID"""
    bot_info = db.get_bot(bot_username)
    return bot_info['owner'] if bot_info else 0

def build_verify_settings(bot_username: str, current_type: str):
    """构建验证设置菜单（文案 + 键盘）"""
    verify_type_labels = {
        'simple': "简单验证码",
        'cf': "Cloudflare 验证",
        'manual': "人工验证",
        'deny': "自动拒绝所有验证",
        'none': "取消验证",
    }
    verify_type_label = verify_type_labels.get(current_type, "简单验证码")
    
    keyboard = [
        [
            InlineKeyboardButton(
                f"{'✅ ' if current_type == 'simple' else ''}简单验证码",
                callback_data=f"verify_simple_{bot_username}"
            )
        ],
        [
            InlineKeyboardButton(
                f"{'✅ ' if current_type == 'cf' else ''}Cloudflare 验证",
                callback_data=f"verify_cf_{bot_username}"
            )
        ],
        [
            InlineKeyboardButton(
                f"{'✅ ' if current_type == 'manual' else ''}人工验证",
                callback_data=f"verify_manual_{bot_username}"
            )
        ],
        [
            InlineKeyboardButton(
                f"{'✅ ' if current_type == 'deny' else ''}自动拒绝所有验证",
                callback_data=f"verify_deny_{bot_username}"
            )
        ],
        [
            InlineKeyboardButton(
                f"{'✅ ' if current_type == 'none' else ''}取消验证",
                callback_data=f"verify_none_{bot_username}"
            )
        ],
        [InlineKeyboardButton("🔙 返回", callback_data=f"info_{bot_username}")]
    ]
    
    info_text = (
        f"🔐 验证设置 - @{bot_username}\n\n"
        f"当前验证方式: {verify_type_label}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"📝 验证方式说明：\n\n"
        f"🔹 简单验证码\n"
        f"• 数学题、逻辑题等\n"
        f"• 支持自定义问答\n"
        f"• 轻量快速\n\n"
        f"🔹 Cloudflare 验证\n"
        f"• 人机验证\n"
        f"• 更强的安全性\n\n"
        f"🔹 人工验证\n"
        f"• 管理员手动审核\n"
        f"• 严格控制用户准入\n\n"
        f"🔹 自动拒绝所有验证\n"
        f"• 拒绝所有新用户进入\n"
        f"• 仅已有验证用户可用\n\n"
        f"🔹 取消验证\n"
        f"• 所有用户无需验证\n"
        f"• 直接使用\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"点击下方按钮切换验证方式："
    )
    
    return info_text, keyboard

async def request_manual_verification(context, message, bot_username: str, owner_id: int):
    """人工验证：通知管理员审核，新用户等待人工审核"""
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "匿名用户"
    user_username = message.from_user.username or ""
    
    # 已有待审核申请则只提示用户，不重复打扰管理员
    pending = db.get_pending_manual_verification(bot_username, user_id)
    if pending:
        await message.reply_text(
            "⏳ 你的验证申请正在审核中\n\n"
            "请耐心等待管理员处理，审核通过后即可使用本服务。",
            parse_mode="HTML"
        )
        return
    
    # 创建申请记录
    db.add_manual_verification(bot_username, user_id, user_name, user_username)
    
    # 通知管理员审核
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    username_line = f"📱 用户名: @{user_username}\n" if user_username else ""
    review_text = (
        f"🔐 <b>人工验证申请</b> (@{bot_username})\n\n"
        f"👤 昵称: {user_name}\n"
        f"{username_line}"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"⏰ 时间: {now}\n\n"
        f"该用户请求使用本机器人，请审核："
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ 通过", callback_data=f"manual_verify_approve:{bot_username}|{user_id}"),
            InlineKeyboardButton("❌ 拒绝", callback_data=f"manual_verify_reject:{bot_username}|{user_id}")
        ]
    ]
    try:
        await context.bot.send_message(
            chat_id=owner_id,
            text=review_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"通知管理员人工审核失败: {e}")
    
    await message.reply_text(
        "📩 已提交人工验证申请\n\n"
        "管理员审核通过后即可使用本服务，请耐心等待。",
        parse_mode="HTML"
    )


# ================== 消息转发逻辑（直连/话题 可切换） ==================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int, bot_username: str):
    """
    - 直连模式(direct):
      用户私聊 -> 转发到 owner 私聊；owner 在私聊里"回复该条转发" -> 回到对应用户
    - 话题模式(forum):
      用户私聊 -> 转发到话题群"用户专属话题"；群里该话题下的消息 -> 回到对应用户
    - /id 功能:
      只有 owner 可以用，显示目标用户信息
    - /block 功能:
      拉黑用户
    - /unblock 功能:
      解除拉黑
    - /blocklist 功能:
      查看黑名单
    """
    try:
        # 支持编辑消息
        message = update.edited_message or update.message
        if not message:
            return
        
        is_edit = update.edited_message is not None
        chat_id = message.chat.id

        # 找到该子机器人的配置
        bot_cfg = get_bot_cfg(owner_id, bot_username)
        if not bot_cfg:
            logger.warning(f"找不到 bot 配置: @{bot_username} for owner {owner_id}")
            return

        mode = bot_cfg.get("mode", "direct")
        forum_group_id = bot_cfg.get("forum_group_id")

        ensure_bot_map(bot_username)

        # ---------- /bl (blocklist) 功能 ----------
        cmd = message.text.strip() if message.text else ""
        if cmd and (cmd == "/bl" or cmd.startswith("/bl ") or cmd.startswith("/bl@") or 
                    cmd == "/blocklist" or cmd.startswith("/blocklist ") or cmd.startswith("/blocklist@")):
            if message.from_user.id != owner_id:
                return

            blocked_users = db.get_blacklist(bot_username)
            if not blocked_users:
                await message.reply_text("📋 黑名单为空")
                return

            text = f"📋 黑名单列表 (@{bot_username})：\n\n"
            for idx, uid in enumerate(blocked_users, 1):
                try:
                    user = await context.bot.get_chat(uid)
                    name = user.full_name or f"@{user.username}" if user.username else "匿名用户"
                    text += f"{idx}. {name} (ID: <code>{uid}</code>)\n"
                except:
                    text += f"{idx}. 用户ID: <code>{uid}</code> (已删除账号)\n"

            await message.reply_text(text, parse_mode="HTML")
            return

        # ---------- /b (block) 功能（拉黑用户）----------
        if cmd and (cmd == "/b" or cmd.startswith("/b ") or cmd.startswith("/b@") or 
                    cmd == "/block" or cmd.startswith("/block ") or cmd.startswith("/block@")):
            if message.from_user.id != owner_id:
                return

            target_user = None

            # 方式1：直接输入 TG ID（如：/b 123456789）
            parts = cmd.split()
            if len(parts) == 2 and parts[1].isdigit():
                target_user = int(parts[1])
            
            # 方式2：回复消息
            elif message.reply_to_message:
                # 直连模式：主人私聊里，回复转发消息
                if mode == "direct" and message.chat.type == "private" and chat_id == owner_id:
                    direct_map = msg_map[bot_username]["direct"]
                    target_user = direct_map.get(str(message.reply_to_message.message_id))

                # 话题模式：群里，回复话题消息
                elif mode == "forum" and message.chat.id == forum_group_id:
                    topic_id = message.reply_to_message.message_thread_id
                    for uid_str, t_id in msg_map[bot_username]["topics"].items():
                        if t_id == topic_id:
                            target_user = int(uid_str)
                            break

            if target_user:
                if add_to_blacklist(bot_username, target_user):
                    await message.reply_text(f"🚫 已将用户 {target_user} 加入黑名单")
                    
                    # 通知到管理频道 - 获取用户信息
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    try:
                        user = await context.bot.get_chat(target_user)
                        user_username = user.username
                        user_name = user.full_name or "匿名用户"
                        # 优先使用 @用户名
                        if user_username:
                            user_display = f"@{user_username}"
                        else:
                            user_display = f"<a href='tg://user?id={target_user}'>{user_name}</a>"
                        log_text = f"🚫 Bot @{bot_username} 拉黑用户 {user_display} (ID: <code>{target_user}</code>) · {now}"
                    except:
                        # 如果获取失败，仅显示ID
                        log_text = f"🚫 Bot @{bot_username} 拉黑用户 ID: <code>{target_user}</code> · {now}"
                    await send_admin_log(log_text)
                else:
                    await message.reply_text(f"⚠️ 用户 {target_user} 已在黑名单中")
            else:
                await message.reply_text("⚠️ 请回复用户消息或输入：/b <TG_ID>")

            return

        # ---------- /ub (unblock) 功能（解除拉黑）----------
        if cmd and (cmd == "/ub" or cmd.startswith("/ub ") or cmd.startswith("/ub@") or 
                    cmd == "/unblock" or cmd.startswith("/unblock ") or cmd.startswith("/unblock@")):
            if message.from_user.id != owner_id:
                return

            target_user = None

            # 方式1：直接输入 TG ID（如：/ub 123456789）
            parts = cmd.split()
            if len(parts) == 2 and parts[1].isdigit():
                target_user = int(parts[1])
            
            # 方式2：回复消息
            elif message.reply_to_message:
                # 直连模式
                if mode == "direct" and message.chat.type == "private" and chat_id == owner_id:
                    direct_map = msg_map[bot_username]["direct"]
                    target_user = direct_map.get(str(message.reply_to_message.message_id))

                # 话题模式
                elif mode == "forum" and message.chat.id == forum_group_id:
                    topic_id = message.reply_to_message.message_thread_id
                    for uid_str, t_id in msg_map[bot_username]["topics"].items():
                        if t_id == topic_id:
                            target_user = int(uid_str)
                            break

            if target_user:
                if remove_from_blacklist(bot_username, target_user):
                    await message.reply_text(f"✅ 已将用户 {target_user} 从黑名单移除")
                    
                    # 通知到管理频道 - 获取用户信息
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    try:
                        user = await context.bot.get_chat(target_user)
                        user_username = user.username
                        user_name = user.full_name or "匿名用户"
                        # 优先使用 @用户名
                        if user_username:
                            user_display = f"@{user_username}"
                        else:
                            user_display = f"<a href='tg://user?id={target_user}'>{user_name}</a>"
                        log_text = f"✅ Bot @{bot_username} 解除拉黑用户 {user_display} (ID: <code>{target_user}</code>) · {now}"
                    except:
                        # 如果获取失败，仅显示ID
                        log_text = f"✅ Bot @{bot_username} 解除拉黑用户 ID: <code>{target_user}</code> · {now}"
                    await send_admin_log(log_text)
                else:
                    await message.reply_text(f"⚠️ 用户 {target_user} 不在黑名单中")
            else:
                await message.reply_text("⚠️ 请回复用户消息或输入：/ub <TG_ID>")

            return

        # ---------- /unverify 功能（取消用户验证）----------
        if cmd and (cmd == "/uv" or cmd.startswith("/uv ") or cmd.startswith("/uv@") or 
                    cmd == "/unverify" or cmd.startswith("/unverify ") or cmd.startswith("/unverify@")):
            if message.from_user.id != owner_id:
                return

            target_user = None

            # 方式1：直接输入 TG ID（如：/uv 123456789）
            parts = cmd.split()
            if len(parts) == 2 and parts[1].isdigit():
                target_user = int(parts[1])
            
            # 方式2：回复消息
            elif message.reply_to_message:
                # 直连模式
                if mode == "direct" and message.chat.type == "private" and chat_id == owner_id:
                    direct_map = msg_map[bot_username]["direct"]
                    target_user = direct_map.get(str(message.reply_to_message.message_id))

                # 话题模式
                elif mode == "forum" and message.chat.id == forum_group_id:
                    topic_id = message.reply_to_message.message_thread_id
                    for uid_str, t_id in msg_map[bot_username]["topics"].items():
                        if t_id == topic_id:
                            target_user = int(uid_str)
                            break

            if target_user:
                if remove_verified_user(bot_username, target_user):
                    # 清除该用户的待验证状态（内存和数据库）
                    verification_key = f"{bot_username}_{target_user}"
                    if verification_key in pending_verifications:
                        del pending_verifications[verification_key]
                    db.remove_pending_verification(bot_username, target_user)
                    
                    await message.reply_text(f"🔓 已取消用户 {target_user} 的验证\n下次发送消息时需要重新验证")
                    
                    # 通知到管理频道 - 获取用户信息
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    try:
                        user = await context.bot.get_chat(target_user)
                        user_username = user.username
                        user_name = user.full_name or "匿名用户"
                        # 优先使用 @用户名
                        if user_username:
                            user_display = f"@{user_username}"
                        else:
                            user_display = f"<a href='tg://user?id={target_user}'>{user_name}</a>"
                        log_text = f"🔓 Bot @{bot_username} 取消用户 {user_display} (ID: <code>{target_user}</code>) 验证 · {now}"
                    except:
                        # 如果获取失败，仅显示ID
                        log_text = f"🔓 Bot @{bot_username} 取消用户 ID: <code>{target_user}</code> 验证 · {now}"
                    await send_admin_log(log_text)
                else:
                    await message.reply_text(f"⚠️ 用户 {target_user} 未验证或不存在")
            else:
                await message.reply_text("⚠️ 请回复用户消息或输入：/uv <TG_ID>")

            return

        # ---------- /id 功能 ----------
        if message.text and message.text.strip().startswith("/id"):
            # 🚫 如果不是主人发的，忽略
            if message.from_user.id != owner_id:
                return  

            target_user = None

            # 直连模式：主人私聊里，必须回复一条转发消息
            if mode == "direct" and message.chat.type == "private" and chat_id == owner_id and message.reply_to_message:
                direct_map = msg_map[bot_username]["direct"]
                target_user = direct_map.get(str(message.reply_to_message.message_id))

            # 话题模式：群里，必须回复某条消息
            elif mode == "forum" and message.chat.id == forum_group_id and message.reply_to_message:
                topic_id = message.reply_to_message.message_thread_id
                for uid_str, t_id in msg_map[bot_username]["topics"].items():
                    if t_id == topic_id:
                        target_user = int(uid_str)
                        break

            # 如果找到了用户，展示信息；否则静默忽略
            if target_user:
                try:
                    user = await context.bot.get_chat(target_user)
                    is_blocked = is_blacklisted(bot_username, user.id)
                    user_verified = is_verified(bot_username, user.id)
                    
                    # 状态显示
                    status_parts = []
                    if is_blocked:
                        status_parts.append("🚫 已拉黑")
                    else:
                        status_parts.append("✅ 正常")
                    
                    if user_verified:
                        status_parts.append("🔓 已验证")
                    else:
                        status_parts.append("🔒 未验证")
                    
                    text = (
                        f"━━━━━━━━━━━━━━\n"
                        f"👤 <b>User Info</b>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🆔 <b>TG_ID:</b> <code>{user.id}</code>\n"
                        f"👤 <b>全   名:</b> {user.first_name} {user.last_name or ''}\n"
                        f"🔗 <b>用户名:</b> @{user.username if user.username else '(无)'}\n"
                        f"🛡 <b>状   态:</b> {' | '.join(status_parts)}\n"
                        f"━━━━━━━━━━━━━━"
                    )

                    # 根据状态显示不同按钮
                    buttons = []
                    
                    # 第一行：拉黑/解除拉黑
                    if is_blocked:
                        buttons.append([InlineKeyboardButton("✅ 解除拉黑", callback_data=f"unblock_{bot_username}_{user.id}")])
                    else:
                        buttons.append([InlineKeyboardButton("🚫 拉黑用户", callback_data=f"block_{bot_username}_{user.id}")])
                    
                    # 第二行：取消验证（仅已验证用户显示）
                    if user_verified:
                        buttons.append([InlineKeyboardButton("🔓 取消验证", callback_data=f"unverify_{bot_username}_{user.id}")])
                    
                    # 第三行：复制UID
                    buttons.append([InlineKeyboardButton("📋 复制 UID", switch_inline_query_current_chat=str(user.id))])
                    
                    keyboard = InlineKeyboardMarkup(buttons)

                    await message.reply_text(
                        text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_markup=keyboard
                    )
                except Exception as e:
                    await message.reply_text(f"❌ 获取用户信息失败: {e}")

            return  # ✅ 不提示、别人也用不了

        # ---------- 验证码检查（普通用户） ----------
        if message.chat.type == "private" and chat_id != owner_id:
            user_id = message.from_user.id
            verification_key = f"{bot_username}_{user_id}"
            
            logger.info(f"[验证检查] Bot: @{bot_username}, 用户: {user_id}, 已验证: {is_verified(bot_username, user_id)}")
            
            # 获取 Bot 配置以确定验证类型
            bot_info = db.get_bot(bot_username)
            verification_type = bot_info.get('verification_type', 'simple') if bot_info else 'simple'
            
            # 自动拒绝模式：拒绝所有未验证用户
            if verification_type == 'deny' and not is_verified(bot_username, user_id):
                await reply_and_auto_delete(
                    message,
                    "⛔ 该机器人暂不接受新用户，已拒绝你的访问。\n\n"
                    "如果你是已有用户，请联系管理员。",
                    delay=5
                )
                logger.info(f"[拒绝] 用户 {user_id} 被拒绝 (@{bot_username}) - deny 模式")
                return
            
            # 如果用户未验证（取消验证模式直接放行）
            if not is_verified(bot_username, user_id) and verification_type != 'none':
                # 如果是 CF 验证模式
                if verification_type == 'cf':
                    # CF 模式下，用户发送任何文本（除了命令）都视为未验证，直接提示验证
                    # 创建验证令牌
                    user_name = message.from_user.full_name or "匿名用户"
                    user_username = message.from_user.username or ""
                    
                    # 发送验证消息（带按钮）
                    message_text = (
                        "🔐 <b>Cloudflare 验证</b>\n\n"
                        "欢迎使用本机器人！\n"
                        "为确保安全，首次使用需要完成验证。\n\n"
                        "📝 请点击下方按钮完成验证：\n\n"
                        "⏰ 验证链接有效期：<b>5分钟</b>\n"
                        "🔗 验证完成后，返回此处继续使用"
                    )
                    
                    sent_message = await message.reply_text(
                        message_text,
                        parse_mode="HTML"
                    )
                    
                    token = db.create_verification_token(bot_username, user_id, user_name, user_username, sent_message.message_id)
                    
                    if token:
                        verify_url = f"{os.environ.get('VERIFY_SERVER_URL', 'http://localhost:5000')}/verify/{token}"
                        keyboard = [[InlineKeyboardButton("🔗 点击验证", url=verify_url)]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        await sent_message.edit_text(
                            message_text,
                            parse_mode="HTML",
                            reply_markup=reply_markup
                        )
                    else:
                         await message.reply_text("❌ 生成验证链接失败，请稍后重试或联系管理员")
                    
                    return

                # 人工验证模式：提交申请，等待管理员审核
                if verification_type == 'manual':
                    await request_manual_verification(context, message, bot_username, owner_id)
                    return

                # 简单验证码模式：检查是否有待验证的验证码（优先从数据库读取）
                expected_captcha = db.get_pending_verification(bot_username, user_id)
                
                # 如果数据库中没有，检查内存
                if not expected_captcha and verification_key in pending_verifications:
                    expected_captcha = pending_verifications[verification_key]
                
                if expected_captcha:
                    user_input = message.text.strip() if message.text else ""
                    
                    logger.info(f"[验证码输入] 用户 {user_id} 输入: '{user_input}', 期望: '{expected_captcha}'")
                    
                    # 验证码正确
                    if user_input == expected_captcha:
                        # 获取用户信息
                        user_name = message.from_user.full_name or "匿名用户"
                        user_username = message.from_user.username or ""
                        
                        # 添加到已验证用户（包含用户信息）
                        add_verified_user(bot_username, user_id, user_name, user_username)
                        
                        # 💾 从数据库和内存中删除待验证记录
                        db.remove_pending_verification(bot_username, user_id)
                        pending_verifications.pop(verification_key, None)
                        
                        # 🔧 为 owner 设置命令菜单（如果之前没设置成功）
                        if user_id == owner_id:
                            try:
                                commands = [
                                    BotCommand("start", "开始使用"),
                                    BotCommand("id", "查看用户"),
                                    BotCommand("b", "拉黑用户"),
                                    BotCommand("ub", "解除拉黑"),
                                    BotCommand("bl", "查看黑名单"),
                                    BotCommand("uv", "取消用户验证")
                                ]
                                await context.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=owner_id))
                                logger.info(f"✅ 已为 @{bot_username} 的拥有者（ID: {owner_id}）设置专属命令菜单")
                            except Exception as cmd_err:
                                logger.warning(f"设置命令菜单失败: {cmd_err}")
                        
                        # 使用优先级欢迎语：用户自定义 > 管理员全局 > 系统默认
                        welcome_msg = get_welcome_message(bot_username)
                        await message.reply_text(welcome_msg)
                        
                        # 通知Bot的主人（owner）
                        now = datetime.now().strftime("%Y-%m-%d %H:%M")
                        
                        # 构建用户显示：统一风格（多行分开显示）
                        notification_text = f"✅ 新用户验证成功\n\n"
                        notification_text += f"👤 昵称: {user_name}\n"
                        if user_username:
                            notification_text += f"📱 用户名: @{user_username}\n"
                        notification_text += (
                            f"🆔 ID: <code>{user_id}</code>\n"
                            f"🤖 Bot: @{bot_username}\n"
                            f"⏰ {now}"
                        )
                        
                        # 仅发送给Bot的主人
                        try:
                            await context.bot.send_message(
                                chat_id=owner_id,
                                text=notification_text,
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.error(f"通知Bot主人失败: {e}")
                        
                        return
                    else:
                        # 验证码错误 - 不显示正确答案！
                        await reply_and_auto_delete(
                            message, 
                            f"❌ 验证码错误！\n\n请仔细检查后重新输入\n或发送 /start 获取新的验证题", 
                            delay=5
                        )
                        return
                else:
                    # 没有待验证的验证码，生成新的
                    logger.info(f"[生成验证码] 用户 {user_id} 首次发送消息，生成验证码")
                    captcha_data = generate_captcha()
                    
                    # 💾 保存到数据库和内存
                    db.add_pending_verification(bot_username, user_id, captcha_data['answer'])
                    pending_verifications[verification_key] = captcha_data['answer']
                    logger.info(f"[验证码] 类型: {captcha_data['type']}, 答案: {captcha_data['answer']}")
                    
                    # 根据验证码类型构建消息
                    retry_captcha_type = captcha_data['type']
                    
                    if retry_captcha_type == 'math':
                        message_text = f"🔐 数学验证\n\n你还未通过验证。\n\n📝 请计算：<b>{captcha_data['question']}</b>\n\n💡 输入计算结果或 /start 换题"
                    elif retry_captcha_type == 'sequence':
                        message_text = f"🔐 逻辑验证\n\n你还未通过验证。\n\n📝 {captcha_data['question']}\n\n💡 观察规律或 /start 换题"
                    elif retry_captcha_type == 'chinese':
                        message_text = f"🔐 中文数字验证\n\n你还未通过验证。\n\n📝 中文数字：<b>{captcha_data['display']}</b>\n\n💡 {captcha_data['question']}或 /start 换题"
                    elif retry_captcha_type == 'logic':
                        message_text = f"🔐 智力验证\n\n你还未通过验证。\n\n📝 {captcha_data['question']}\n\n💡 简单逻辑题或 /start 换题"
                    elif retry_captcha_type == 'time':
                        message_text = f"🔐 时间验证\n\n你还未通过验证。\n\n📝 时间：<b>{captcha_data['display']}</b>\n\n💡 {captcha_data['question']}或 /start 换题"
                    else:
                        message_text = f"🔐 验证\n\n你还未通过验证。\n\n📝 {captcha_data['question']}\n\n💡 请输入答案或 /start 换题"
                    
                    await message.reply_text(message_text, parse_mode="HTML")
                    return

        # ---------- 黑名单拦截 ----------
        if message.chat.type == "private" and chat_id != owner_id:
            if is_blacklisted(bot_username, chat_id):
                # 被拉黑用户发消息，静默忽略或返回提示
                await reply_and_auto_delete(message, "⚠️ 你已被管理员拉黑，消息无法发送。", delay=5)
                logger.info(f"拦截黑名单用户 {chat_id} 的消息 (@{bot_username})")
                return

        # ---------- 直连模式 ----------
        if mode == "direct":
            # 普通用户发私聊 -> 转给主人
            if message.chat.type == "private" and chat_id != owner_id:
                user_msg_key = f"{chat_id}_{message.message_id}"
                
                if is_edit:
                    # 如果是编辑消息，尝试编辑之前发送的消息
                    forward_msg_id = msg_map[bot_username]["user_to_forward"].get(user_msg_key)
                    if forward_msg_id:
                        try:
                            # 编辑消息 (只能编辑文本)
                            if message.text:
                                # 获取用户名
                                username = f"@{message.from_user.username}" if message.from_user.username else ""
                                display_name = message.from_user.full_name or '未知'
                                user_header = f"👤 {display_name} ({username})" if username else f"👤 {display_name}"
                                
                                await context.bot.edit_message_text(
                                    chat_id=owner_id,
                                    message_id=forward_msg_id,
                                    text=f"{user_header}\n\n{message.text} [✏️已编辑]"
                                )
                                logger.info(f"用户 {chat_id} 编辑消息成功")
                                await reply_and_auto_delete(message, "✅ 编辑同步成功", delay=3)
                            else:
                                # 如果不是文本消息，无法直接编辑，发送新消息提示
                                await context.bot.send_message(
                                    chat_id=owner_id,
                                    text=f"✏️ 用户 {message.from_user.full_name or '未知'} (ID: {chat_id}) 编辑了消息\n(非文本消息无法同步编辑)"
                                )
                                await reply_and_auto_delete(message, "⚠️ 非文本消息无法同步编辑", delay=3)
                        except Exception as e:
                            logger.error(f"编辑消息失败: {e}")
                            # 如果编辑失败，发送提示
                            await context.bot.send_message(
                                chat_id=owner_id,
                                text=f"✏️ 用户 {message.from_user.full_name or '未知'} (ID: {chat_id}) 编辑了消息，但无法同步编辑"
                            )
                            await reply_and_auto_delete(message, f"⚠️ 编辑同步失败", delay=3)
                        return
                else:
                    # 新消息 - 发送文本消息而不是转发(这样可以编辑)
                    # 获取用户名
                    username = f"@{message.from_user.username}" if message.from_user.username else ""
                    display_name = message.from_user.full_name or '未知'
                    user_header = f"👤 {display_name} ({username})" if username else f"👤 {display_name}"
                    
                    if message.text:
                        # 文本消息：发送可编辑的消息
                        sent_msg = await context.bot.send_message(
                            chat_id=owner_id,
                            text=f"{user_header}\n\n{message.text}"
                        )
                        # 💾 保存到数据库和内存
                        msg_map[bot_username]["direct"][str(sent_msg.message_id)] = chat_id
                        db.set_mapping(bot_username, "direct", str(sent_msg.message_id), str(chat_id), chat_id)
                        
                        msg_map[bot_username]["user_to_forward"][user_msg_key] = sent_msg.message_id
                        db.set_mapping(bot_username, "user_forward", user_msg_key, str(sent_msg.message_id), chat_id)
                        
                        msg_map[bot_username]["forward_to_user"][str(sent_msg.message_id)] = user_msg_key
                        db.set_mapping(bot_username, "forward_user", str(sent_msg.message_id), user_msg_key, chat_id)
                    else:
                        # 非文本消息：先发送用户信息，再转发原消息
                        await context.bot.send_message(
                            chat_id=owner_id,
                            text=user_header
                        )
                        fwd_msg = await context.bot.forward_message(
                            chat_id=owner_id,
                            from_chat_id=chat_id,
                            message_id=message.message_id
                        )
                        # 💾 保存到数据库和内存
                        msg_map[bot_username]["direct"][str(fwd_msg.message_id)] = chat_id
                        db.set_mapping(bot_username, "direct", str(fwd_msg.message_id), str(chat_id), chat_id)
                    
                    await reply_and_auto_delete(message, "✅ 已成功发送", delay=3)
                return

            # 主人在私聊里回复 -> 回用户
            if message.chat.type == "private" and chat_id == owner_id and message.reply_to_message:
                direct_map = msg_map[bot_username]["direct"]
                target_user = direct_map.get(str(message.reply_to_message.message_id))
                
                if target_user:
                    owner_msg_key = f"{owner_id}_{message.message_id}"
                    
                    if is_edit:
                        # 主人编辑了回复，尝试编辑发送给用户的消息
                        user_msg_id = msg_map[bot_username]["owner_to_user"].get(owner_msg_key)
                        if user_msg_id:
                            try:
                                if message.text:
                                    await context.bot.edit_message_text(
                                        chat_id=target_user,
                                        message_id=user_msg_id,
                                        text=message.text
                                    )
                                    logger.info(f"主人编辑回复成功")
                                    await reply_and_auto_delete(message, "✅ 编辑同步成功", delay=2)
                                else:
                                    await reply_and_auto_delete(message, "⚠️ 非文本消息无法编辑", delay=3)
                            except Exception as e:
                                logger.error(f"编辑回复失败: {e}")
                                await reply_and_auto_delete(message, f"⚠️ 编辑失败: {e}", delay=5)
                        return
                    else:
                        # 新回复
                        sent_msg = await context.bot.copy_message(
                            chat_id=target_user,
                            from_chat_id=owner_id,
                            message_id=message.message_id
                        )
                        # 💾 保存映射关系到数据库和内存
                        msg_map[bot_username]["owner_to_user"][owner_msg_key] = sent_msg.message_id
                        db.set_mapping(bot_username, "owner_user", owner_msg_key, str(sent_msg.message_id), int(target_user))
                        await reply_and_auto_delete(message, "✅ 回复已送达", delay=2)
                else:
                    if not is_edit:
                        await reply_and_auto_delete(message, "⚠️ 找不到对应的用户映射。", delay=5)
                return

        # ---------- 话题模式 ----------
        elif mode == "forum":
            logger.info(f"[话题模式] Bot: @{bot_username}, forum_group_id: {forum_group_id}")
            
            if not forum_group_id:
                logger.warning(f"[话题模式] 未设置群ID，无法转发")
                if message.chat.type == "private" and chat_id != owner_id:
                    await reply_and_auto_delete(message, "⚠️ 主人未设置话题群，暂无法转发。", delay=5)
                return

            topics = msg_map[bot_username]["topics"]

            # 普通用户发私聊 -> 转到对应话题
            if message.chat.type == "private" and chat_id != owner_id:
                logger.info(f"[话题模式] 收到用户 {chat_id} 的私聊消息，准备转发到群 {forum_group_id}")
                uid_key = str(chat_id)
                topic_id = topics.get(uid_key)
                user_msg_key = f"{chat_id}_{message.message_id}"

                # 若无映射，先尝试从数据库单独获取（双重保险）
                if not topic_id:
                    existing_topic = db.get_mapping(bot_username, "topic", uid_key)
                    if existing_topic and existing_topic.isdigit():
                        topic_id = int(existing_topic)
                        topics[uid_key] = topic_id  # 回写内存
                        logger.info(f"[话题模式] 从数据库恢复了话题映射: user {chat_id} -> topic {topic_id}")

                # 若仍无映射，才创建新话题
                if not topic_id:
                    display_name = (
                        message.from_user.full_name
                        or (f"@{message.from_user.username}" if message.from_user.username else None)
                        or "匿名用户"
                    )
                    try:
                        topic = await context.bot.create_forum_topic(
                            chat_id=forum_group_id,
                            name=f"{display_name}"
                        )
                        topic_id = topic.message_thread_id
                        # 💾 保存到数据库和内存
                        topics[uid_key] = topic_id
                        db.set_mapping(bot_username, "topic", uid_key, str(topic_id), chat_id)
                    except Exception as e:
                        logger.error(f"创建话题失败: {e}")
                        await reply_and_auto_delete(message, "❌ 创建话题失败，请联系管理员。", delay=5)
                        return

                # 转发到话题
                try:
                    if is_edit:
                        # 如果是编辑消息，尝试编辑之前发送的消息
                        forward_msg_id = msg_map[bot_username]["user_to_forward"].get(user_msg_key)
                        if forward_msg_id:
                            try:
                                if message.text:
                                    # 话题模式：不显示用户信息(话题名称已经是用户名)
                                    await context.bot.edit_message_text(
                                        chat_id=forum_group_id,
                                        message_id=forward_msg_id,
                                        text=f"{message.text} [✏️已编辑]"
                                    )
                                    logger.info(f"[话题模式] 用户 {chat_id} 编辑消息成功")
                                    # 话题模式：直接发送消息给用户，不使用reply
                                    await send_and_auto_delete(context, chat_id, "✅ 编辑同步成功", delay=3)
                                else:
                                    # 非文本消息无法编辑
                                    await context.bot.send_message(
                                        chat_id=forum_group_id,
                                        message_thread_id=topic_id,
                                        text=f"✏️ 用户编辑了消息 (非文本消息无法同步编辑)"
                                    )
                                    await send_and_auto_delete(context, chat_id, "⚠️ 非文本消息无法同步编辑", delay=3)
                            except Exception as e:
                                logger.error(f"[话题模式] 编辑消息失败: {e}")
                                # 编辑失败，发送提示
                                await context.bot.send_message(
                                    chat_id=forum_group_id,
                                    message_thread_id=topic_id,
                                    text="✏️ 用户编辑了消息，但无法同步编辑"
                                )
                                await send_and_auto_delete(context, chat_id, "⚠️ 编辑同步失败", delay=3)
                        return
                    else:
                        # 新消息
                        logger.info(f"[话题模式] 转发消息到话题 {topic_id}")
                        
                        if message.text:
                            # 文本消息：发送可编辑的消息(话题模式不显示用户信息)
                            sent_msg = await context.bot.send_message(
                                chat_id=forum_group_id,
                                message_thread_id=topic_id,
                                text=message.text
                            )
                            # 💾 保存映射关系到数据库和内存
                            msg_map[bot_username]["user_to_forward"][user_msg_key] = sent_msg.message_id
                            db.set_mapping(bot_username, "user_forward", user_msg_key, str(sent_msg.message_id), chat_id)
                            
                            msg_map[bot_username]["forward_to_user"][str(sent_msg.message_id)] = user_msg_key
                            db.set_mapping(bot_username, "forward_user", str(sent_msg.message_id), user_msg_key, chat_id)
                        else:
                            # 非文本消息：直接转发(话题模式)
                            await context.bot.forward_message(
                                chat_id=forum_group_id,
                                from_chat_id=chat_id,
                                message_id=message.message_id,
                                message_thread_id=topic_id
                            )
                        
                        logger.info(f"[话题模式] 转发成功")
                        await reply_and_auto_delete(message, "✅ 已转交客服处理", delay=2)

                except BadRequest as e:
                    low = str(e).lower()
                    if ("message thread not found" in low) or ("topic not found" in low):
                        try:
                            display_name = (
                                message.from_user.full_name
                                or (f"@{message.from_user.username}" if message.from_user.username else None)
                                or "匿名用户"
                            )
                            topic = await context.bot.create_forum_topic(
                                chat_id=forum_group_id,
                                name=f"{display_name}"
                            )
                            topic_id = topic.message_thread_id
                            # 💾 保存到数据库和内存
                            topics[uid_key] = topic_id
                            db.set_mapping(bot_username, "topic", uid_key, str(topic_id), chat_id)

                            await context.bot.forward_message(
                                chat_id=forum_group_id,
                                from_chat_id=chat_id,
                                message_id=message.message_id,
                                message_thread_id=topic_id
                            )
                            await reply_and_auto_delete(message, "✅ 已转交客服处理（话题已重建）", delay=2)

                        except Exception as e2:
                            logger.error(f"重建话题失败: {e2}")
                            await reply_and_auto_delete(message, "❌ 转发失败，重建话题也未成功。", delay=5)
                    else:
                        logger.error(f"转发到话题失败: {e}")
                        await reply_and_auto_delete(message, "❌ 转发到话题失败，请检查权限。", delay=5)
                return

            # 群里该话题下的消息 -> 回到用户
            if message.chat.id == forum_group_id and getattr(message, "is_topic_message", False):
                topic_id = message.message_thread_id
                logger.info(f"[话题模式] 收到群消息，topic_id: {topic_id}, 查找对应用户")
                target_uid = None
                for uid_str, t_id in topics.items():
                    if t_id == topic_id:
                        target_uid = int(uid_str)
                        break
                if target_uid:
                    try:
                        owner_msg_key = f"{forum_group_id}_{message.message_id}"
                        
                        if is_edit:
                            # 主人编辑了消息，尝试编辑发送给用户的消息
                            user_msg_id = msg_map[bot_username]["owner_to_user"].get(owner_msg_key)
                            if user_msg_id:
                                try:
                                    if message.text:
                                        await context.bot.edit_message_text(
                                            chat_id=target_uid,
                                            message_id=user_msg_id,
                                            text=message.text
                                        )
                                        logger.info(f"[话题模式] 主人编辑回复成功")
                                        # 话题模式下主人在群里编辑，给一个简单的反馈(不使用reply_and_auto_delete，因为可能没有reply_to_message)
                                        try:
                                            sent = await message.reply_text("✅ 编辑同步成功")
                                            await asyncio.sleep(2)
                                            await sent.delete()
                                        except:
                                            pass
                                    else:
                                        logger.warning(f"[话题模式] 非文本消息无法编辑")
                                except Exception as e:
                                    logger.error(f"[话题模式] 编辑回复失败: {e}")
                        else:
                            # 新消息
                            logger.info(f"[话题模式] 找到用户 {target_uid}，准备发送")
                            sent_msg = await context.bot.copy_message(
                                chat_id=target_uid,
                                from_chat_id=forum_group_id,
                                message_id=message.message_id
                            )
                            # 💾 保存映射关系到数据库和内存
                            msg_map[bot_username]["owner_to_user"][owner_msg_key] = sent_msg.message_id
                            db.set_mapping(bot_username, "owner_user", owner_msg_key, str(sent_msg.message_id), target_uid)
                            logger.info(f"[话题模式] 回复发送成功")
                    except Exception as e:
                        logger.error(f"群->用户 复制失败: {e}")
                else:
                    logger.warning(f"[话题模式] 未找到 topic_id {topic_id} 对应的用户")
                return

    except Exception as e:
        logger.error(f"[{bot_username}] 转发错误: {e}")
        
# ================== 动态管理 Bot（添加/删除/配置） ==================
async def token_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """监听用户输入的 token 或话题群ID 或广播消息"""
    # ----- 等待广播消息 -----
    if context.user_data.get("waiting_broadcast"):
        if not is_admin(update.message.from_user.id):
            await reply_and_auto_delete(update.message, "⚠️ 仅管理员可用", delay=5)
            context.user_data.pop("waiting_broadcast", None)
            return
        
        broadcast_msg = update.message.text.strip()
        if not broadcast_msg:
            await reply_and_auto_delete(update.message, "❌ 消息内容不能为空", delay=5)
            return
        
        context.user_data.pop("waiting_broadcast", None)
        
        # 获取所有托管机器人的用户（owner）
        all_owners = list(bots_data.keys())
        
        if not all_owners:
            await update.message.reply_text("⚠️ 暂无托管用户")
            return
        
        # 发送广播
        success_count = 0
        fail_count = 0
        fail_users = []
        
        status_msg = await update.message.reply_text(
            f"📢 开始广播...\n\n"
            f"总用户数: {len(all_owners)}\n"
            f"成功: {success_count}\n"
            f"失败: {fail_count}"
        )
        
        for idx, owner_id in enumerate(all_owners, 1):
            try:
                owner_id_int = int(owner_id)
                # 使用管理机器人发送消息
                await context.bot.send_message(
                    chat_id=owner_id_int,
                    text=f"📢 系统广播\n\n{broadcast_msg}"
                )
                success_count += 1
            except Exception as e:
                fail_count += 1
                fail_users.append((owner_id, str(e)))
                logger.error(f"广播失败 - 用户 {owner_id}: {e}")
            
            # 每10个用户更新一次状态
            if idx % 10 == 0:
                try:
                    await status_msg.edit_text(
                        f"📢 广播中...\n\n"
                        f"进度: {idx}/{len(all_owners)}\n"
                        f"成功: {success_count}\n"
                        f"失败: {fail_count}"
                    )
                except:
                    pass
        
        # 最终结果
        result_text = (
            f"✅ 广播完成\n\n"
            f"总用户数: {len(all_owners)}\n"
            f"✅ 成功: {success_count}\n"
            f"❌ 失败: {fail_count}"
        )
        
        if fail_users and len(fail_users) <= 10:
            result_text += "\n\n失败列表："
            for owner_id, reason in fail_users:
                result_text += f"\n• ID:{owner_id} - {reason}"
        
        await status_msg.edit_text(result_text)
        
        # 记录到管理频道
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        admin_username = update.message.from_user.username
        admin_display = f"@{admin_username}" if admin_username else f"管理员 {update.message.from_user.id}"
        await send_admin_log(
            f"📢 {admin_display} 发送广播\n"
            f"成功: {success_count}/{len(all_owners)}\n"
            f"时间: {now}"
        )
        
        return
    
    # ----- 等待设置欢迎语 -----
    action = context.user_data.get("action")
    
    # 用户设置机器人欢迎语
    if action == "set_welcome" and update.message and update.message.text:
        bot_username = context.user_data.get("bot_username")
        owner_id = str(update.message.chat.id)
        welcome_text = update.message.text.strip()
        
        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await update.message.reply_text("⚠️ 找不到这个 Bot")
            context.user_data.pop("action", None)
            context.user_data.pop("bot_username", None)
            return
        
        # 保存欢迎语到数据库
        if db.update_bot_welcome(bot_username, welcome_text):
            # 更新内存中的数据
            target_bot["welcome_msg"] = welcome_text
            load_bots()
            
            await update.message.reply_text(
                f"✅ 已为 @{bot_username} 设置自定义欢迎语\n\n"
                f"━━━━━━━━━━━━━━\n"
                f"{welcome_text[:100]}{'...' if len(welcome_text) > 100 else ''}\n"
                f"━━━━━━━━━━━━━━\n\n"
                f"💡 用户下次发送 /start 时将看到此欢迎语"
            )
            
            # 通知管理员
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            user_username = update.message.from_user.username
            user_display = f"@{user_username}" if user_username else f"用户ID: {owner_id}"
            await send_admin_log(f"✏️ {user_display} (ID: <code>{owner_id}</code>) 为 @{bot_username} 设置了自定义欢迎语 · {now}")
        else:
            await update.message.reply_text("❌ 设置失败，请稍后重试")
        
        context.user_data.pop("action", None)
        context.user_data.pop("bot_username", None)
        return
    
    # 管理员设置全局欢迎语
    if action == "set_global_welcome" and update.message and update.message.text:
        if not is_admin(update.message.from_user.id):
            await update.message.reply_text("⚠️ 无权限操作")
            context.user_data.pop("action", None)
            return
        
        welcome_text = update.message.text.strip()
        
        # 保存全局欢迎语
        if db.set_global_welcome(welcome_text):
            await update.message.reply_text(
                f"✅ 已设置全局欢迎语\n\n"
                f"━━━━━━━━━━━━━━\n"
                f"{welcome_text[:200]}{'...' if len(welcome_text) > 200 else ''}\n"
                f"━━━━━━━━━━━━━━\n\n"
                f"💡 所有未自定义欢迎语的机器人将使用此欢迎语"
            )
            
            # 通知管理员
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            await send_admin_log(f"📝 管理员设置了全局欢迎语 · {now}")
        else:
            await update.message.reply_text("❌ 设置失败，请稍后重试")
        
        context.user_data.pop("action", None)
        return
    
    # ----- 等待设置话题群ID -----
    pending_bot_forum = context.user_data.get("waiting_forum_for")
    if pending_bot_forum and update.message and update.message.text:
        bot_username = pending_bot_forum["bot_username"]
        owner_id = str(update.message.chat.id)
        gid_text = update.message.text.strip()
        
        # 验证群ID格式
        try:
            gid = int(gid_text)
        except ValueError:
            await reply_and_auto_delete(update.message, "❌ 群ID无效，请输入数字。", delay=8)
            return
        
        # 验证格式：必须是 -1 开头的 13 位数字
        gid_str = str(gid)
        if not (gid_str.startswith("-100") and len(gid_str) == 14):
            await update.message.reply_text(
                f"❌ 群ID格式错误！\n\n"
                f"你输入的：<code>{gid}</code>\n\n"
                f"正确格式要求：\n"
                f"• 必须以 -100 开头\n"
                f"• 总共 14 位数字（包括负号后13位）\n"
                f"• 示例：-1004877845787\n\n"
                f"⚠️ 注意：\n"
                f"• 请在群组设置页面获取群ID\n"
                f"• 不要在话题模式下复制的 500 开头的话题ID\n"
                f"• 话题ID无效，需要的是群组ID",
                parse_mode="HTML"
            )
            return

        # 写入该 bot 的 forum_group_id
        for b in bots_data.get(owner_id, {}).get("bots", []):
            if b["bot_username"] == bot_username:
                b["forum_group_id"] = gid
                
                # 💾 保存到数据库
                db.update_bot_forum_id(bot_username, gid)
                save_bots()
                
                await update.message.reply_text(f"✅ 已为 @{bot_username} 设置话题群ID：<code>{gid}</code>", parse_mode="HTML")
                # 宿主通知
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                user_username = update.message.from_user.username
                user_display = f"@{user_username}" if user_username else f"用户ID: {owner_id}"
                await send_admin_log(f"🛠 {user_display} (ID: <code>{owner_id}</code>) 为 @{bot_username} 设置话题群ID为 {gid} · {now}")
                break
        context.user_data.pop("waiting_forum_for", None)
        return

    # ----- 等待添加子Bot Token -----
    if not context.user_data.get("waiting_token"):
        return

    token = update.message.text.strip()
    context.user_data["waiting_token"] = False

    try:
        tmp_app = Application.builder().token(token).build()
        bot_info = await tmp_app.bot.get_me()
        bot_username = bot_info.username
    except Exception:
        await reply_and_auto_delete(update.message, "❌ Token 无效，请检查。", delay=10)
        return

    owner_id = str(update.message.chat.id)
    owner_username = update.message.from_user.username or ""

    # 初始化 owner 节点
    bots_data.setdefault(owner_id, {"username": owner_username, "bots": []})

    # 重复检查
    if any(b["token"] == token for b in bots_data[owner_id]["bots"]):
        await reply_and_auto_delete(update.message, "⚠️ 这个 Bot 已经添加过了。", delay=10)
        return

    # 记录 bot（默认直连模式）
    bots_data[owner_id]["bots"].append({
        "token": token,
        "bot_username": bot_username,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "mode": "direct",
        "forum_group_id": None
    })
    
    # 💾 保存到数据库（持久化）
    db.add_bot(bot_username, token, int(owner_id), welcome_msg='')
    save_bots()
    
    # 🔄 触发静默备份（不推送通知）
    trigger_backup(silent=True)

    # 启动子 Bot
    new_app = Application.builder().token(token).build()
    new_app.add_handler(CommandHandler("start", subbot_start))
    # 处理普通消息
    new_app.add_handler(MessageHandler(filters.ALL, partial(handle_message, owner_id=int(owner_id), bot_username=bot_username)))
    # 处理编辑消息
    new_app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, partial(handle_message, owner_id=int(owner_id), bot_username=bot_username)))
    # 💡 添加回调处理器（处理 /id 命令的按钮）
    new_app.add_handler(CallbackQueryHandler(callback_handler))

    running_apps[bot_username] = new_app
    await new_app.initialize()
    await new_app.start()
    
    # 设置子机器人的命令菜单（仅对绑定用户显示）
    try:
        # 先清除所有默认命令（全局）
        await new_app.bot.delete_my_commands()
        logger.info(f"✅ 已清除 @{bot_username} 的全局命令菜单")
        
        # 尝试为 owner 设置命令菜单（如果bot和owner还没对话会失败，这是正常的）
        try:
            commands = [
                BotCommand("start", "开始使用"),
                BotCommand("id", "查看用户"),
                BotCommand("b", "拉黑用户"),
                BotCommand("ub", "解除拉黑"),
                BotCommand("bl", "查看黑名单"),
                BotCommand("uv", "取消用户验证")
            ]
            await new_app.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=owner_id))
            logger.info(f"✅ 已为 @{bot_username} 的拥有者（ID: {owner_id}）设置专属命令菜单")
        except Exception as scope_err:
            # Bot还没和owner对话过，等用户首次/start后会自动设置
            logger.info(f"ℹ️  @{bot_username} 暂未与拥有者建立对话，将在首次对话时设置命令菜单")
    except Exception as e:
        logger.error(f"❌ 设置命令菜单失败: {e}")
    
    await new_app.updater.start_polling()

    await update.message.reply_text(
        f"✅ 已添加并启动 Bot：@{bot_username}\n\n"
        f"🎯 默认模式：私聊模式\n\n"
        f"🔬 可在\"我的机器人 → 进入Bot → 切换模式\"\n\n"
        f"💡 话题模式 必须 设置话题群ID。"
    )

    # 🔔 添加通知（发送到管理频道）
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # 优先使用 @用户名
    user_display = f"@{owner_username}" if owner_username else f"用户ID: {owner_id}"
    log_text = (
        f"🛒 {user_display}\n"
        f"🆔 <code>{owner_id}</code>\n"
        f"🤖 Bot: @{bot_username}\n"
        f"⏰ {now}"
    )
    await send_admin_log(log_text)

# ================== 菜单回调 ==================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    # 🔍 添加日志：记录回调触发
    logger.info(f"[回调] 收到回调: {data}, 来自用户: {query.from_user.id}")
    
    try:
        await query.answer()
    except Exception as e:
        logger.error(f"[回调] query.answer() 失败: {e}")
        return

    # ================== 管理员功能 ==================
    # 查看所有用户（分页）
    if data.startswith("admin_users"):
        if not is_admin(query.from_user.id):
            await query.answer("⚠️ 仅管理员可用", show_alert=True)
            return
        
        # ⏳ 立即显示加载消息（让用户看到反馈）
        try:
            await query.message.edit_text("⏳ 正在加载用户列表，请稍候...")
        except:
            pass
        
        # 解析页码
        page = 0
        if "_" in data:
            parts = data.split("_")
            if len(parts) == 3 and parts[2].isdigit():
                page = int(parts[2])
        
        # 获取所有托管机器人的用户（从 bots_data）
        all_users = []
        for owner_id, owner_data in bots_data.items():
            if owner_data.get("bots"):
                # 获取用户信息（从第一个bot获取）
                bot_usernames = [bot['bot_username'] for bot in owner_data['bots']]
                all_users.append({
                    'owner_id': owner_id,
                    'bot_usernames': bot_usernames,
                    'bot_count': len(bot_usernames)
                })
        
        if not all_users:
            await query.message.edit_text("📋 暂无托管用户", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]]))
            return
        
        # 分页处理（每页15个）
        page_size = 15
        total_pages = (len(all_users) + page_size - 1) // page_size
        page = max(0, min(page, total_pages - 1))
        
        start_idx = page * page_size
        end_idx = min(start_idx + page_size, len(all_users))
        page_users = all_users[start_idx:end_idx]
        
        # 构建用户列表文本
        text = f"👥 托管用户列表（共 {len(all_users)} 人）\n"
        text += f"📄 第 {page + 1}/{total_pages} 页\n\n"
        
        for idx, user_info in enumerate(page_users, start=start_idx + 1):
            # 获取用户信息
            try:
                owner_id_int = int(user_info['owner_id'])
                # 尝试通过任意一个bot获取用户信息
                user_display = f"ID: {owner_id_int}"
                for bot_username in user_info['bot_usernames'][:1]:  # 只取第一个bot
                    if bot_username in running_apps:
                        try:
                            chat = await running_apps[bot_username].bot.get_chat(owner_id_int)
                            if chat.username:
                                user_display = f"@{chat.username}"
                            elif chat.first_name:
                                user_display = chat.first_name
                            break
                        except:
                            pass
            except:
                user_display = f"ID: {user_info['owner_id']}"
            
            # 显示用户的bot列表
            bot_list = ", ".join([f"@{bot}" for bot in user_info['bot_usernames'][:3]])
            if user_info['bot_count'] > 3:
                bot_list += f" 等{user_info['bot_count']}个"
            
            text += f"{idx}. {user_display}，Bot: {bot_list}\n"
        
        # 构建翻页按钮
        keyboard = []
        nav_buttons = []
        
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"admin_users_{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"admin_users_{page + 1}"))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
        
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # 广播通知
    if data == "admin_broadcast":
        if not is_admin(query.from_user.id):
            await query.answer("⚠️ 仅管理员可用", show_alert=True)
            return
        
        await query.message.edit_text(
            "📢 广播通知功能\n\n"
            "请输入要广播的消息内容：\n\n"
            "⚠️ 注意：消息将发送给所有托管机器人的用户",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 取消", callback_data="back_home")]])
        )
        context.user_data["waiting_broadcast"] = True
        return
    
    # 清理失效Bot
    if data == "admin_clean_invalid":
        if not is_admin(query.from_user.id):
            await query.answer("⚠️ 仅管理员可用", show_alert=True)
            return
        
        await query.message.edit_text(
            "🗑️ 正在检测失效的机器人...\n\n"
            "请稍候..."
        )
        
        # 检测所有bot的token有效性
        all_bots = db.get_all_bots()
        invalid_bots = []
        valid_count = 0
        
        for bot_username, bot_info in all_bots.items():
            try:
                # 尝试验证token
                from telegram import Bot
                test_bot = Bot(token=bot_info['token'])
                await test_bot.get_me()
                valid_count += 1
            except Exception as e:
                invalid_bots.append({
                    'username': bot_username,
                    'owner': bot_info['owner'],
                    'token': bot_info['token'][:20] + "...",
                    'error': str(e)
                })
        
        if not invalid_bots:
            await query.message.edit_text(
                f"✅ 检测完成\n\n"
                f"有效机器人: {valid_count} 个\n"
                f"失效机器人: 0 个\n\n"
                f"🎉 所有机器人都正常！",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]])
            )
            return
        
        # 显示失效bot列表
        text = f"🗑️ 失效机器人列表\n\n"
        text += f"✅ 有效: {valid_count} 个\n"
        text += f"❌ 失效: {len(invalid_bots)} 个\n\n"
        
        for idx, bot in enumerate(invalid_bots[:10], 1):  # 最多显示10个
            text += f"{idx}. @{bot['username']}\n"
            text += f"   Owner ID: {bot['owner']}\n\n"
        
        if len(invalid_bots) > 10:
            text += f"\n... 还有 {len(invalid_bots) - 10} 个\n"
        
        keyboard = [
            [InlineKeyboardButton("🗑️ 删除所有失效Bot", callback_data="admin_confirm_clean")],
            [InlineKeyboardButton("🔙 取消", callback_data="back_home")]
        ]
        
        # 保存失效bot列表到上下文
        context.user_data["invalid_bots"] = [bot['username'] for bot in invalid_bots]
        
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # 确认删除失效Bot
    if data == "admin_confirm_clean":
        if not is_admin(query.from_user.id):
            await query.answer("⚠️ 仅管理员可用", show_alert=True)
            return
        
        invalid_bots = context.user_data.get("invalid_bots", [])
        if not invalid_bots:
            await query.answer("⚠️ 没有待清理的机器人", show_alert=True)
            return
        
        await query.message.edit_text(
            f"🗑️ 正在删除 {len(invalid_bots)} 个失效机器人...\n\n"
            "请稍候..."
        )
        
        # 删除失效bot
        deleted_count = 0
        failed_count = 0
        
        for bot_username in invalid_bots:
            try:
                # 从数据库删除
                db.delete_bot(bot_username)
                
                # 从内存删除
                all_bots = db.get_all_bots()
                for owner_id, owner_data in list(bots_data.items()):
                    owner_data['bots'] = [b for b in owner_data['bots'] if b['bot_username'] != bot_username]
                    if not owner_data['bots']:
                        del bots_data[owner_id]
                
                # 停止运行中的bot
                if bot_username in running_apps:
                    try:
                        await running_apps[bot_username].stop()
                        del running_apps[bot_username]
                    except:
                        pass
                
                deleted_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"删除失效bot {bot_username} 失败: {e}")
        
        # 清理上下文
        context.user_data.pop("invalid_bots", None)
        
        # 触发备份
        trigger_backup(silent=True)
        
        result_text = (
            f"✅ 清理完成\n\n"
            f"成功删除: {deleted_count} 个\n"
            f"删除失败: {failed_count} 个\n\n"
            f"已自动触发备份。"
        )
        
        await query.message.edit_text(
            result_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]])
        )
        
        # 记录到管理频道
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        await send_admin_log(
            f"🗑️ 管理员清理失效Bot\n"
            f"成功: {deleted_count} 个\n"
            f"失败: {failed_count} 个\n"
            f"时间: {now}"
        )
        return

    # 新增：处理拉黑/解除拉黑/取消验证按钮
    if data.startswith("block_") or data.startswith("unblock_") or data.startswith("unverify_"):
        try:
            # 确定操作类型
            if data.startswith("block_"):
                action = "block"
                remaining = data[6:]  # 去掉 "block_"
            elif data.startswith("unblock_"):
                action = "unblock"
                remaining = data[8:]  # 去掉 "unblock_"
            else:  # unverify
                action = "unverify"
                remaining = data[9:]  # 去掉 "unverify_"
            
            # 从后往前分割，最后一个 _ 后面是 user_id
            last_underscore = remaining.rfind("_")
            if last_underscore == -1:
                raise ValueError("格式错误：缺少用户ID")
            
            bot_username = remaining[:last_underscore]
            user_id = int(remaining[last_underscore + 1:])
            
            logger.info(f"[回调] 解析成功 - action: {action}, bot: {bot_username}, user: {user_id}")
        except Exception as e:
            logger.error(f"[回调] 解析回调数据失败: {e}, data: {data}")
            await query.message.edit_text(f"❌ 解析数据失败: {e}")
            return

        if action == "block":
            try:
                if add_to_blacklist(bot_username, user_id):
                    await query.message.edit_text(f"🚫 已将用户 {user_id} 加入黑名单")
                    logger.info(f"[回调] 成功拉黑用户: {user_id} (Bot: @{bot_username})")
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    # 获取用户信息
                    try:
                        user = await context.bot.get_chat(user_id)
                        user_username = user.username
                        user_name = user.full_name or "匿名用户"
                        # 优先使用 @用户名
                        if user_username:
                            user_display = f"@{user_username}"
                        else:
                            user_display = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
                        log_text = f"🚫 Bot @{bot_username} 拉黑用户 {user_display} (ID: <code>{user_id}</code>) · {now}"
                    except:
                        # 如果获取失败，仅显示ID
                        log_text = f"🚫 Bot @{bot_username} 拉黑用户 ID: <code>{user_id}</code> · {now}"
                    await send_admin_log(log_text)
                else:
                    await query.message.edit_text(f"⚠️ 用户 {user_id} 已在黑名单中")
                    logger.info(f"[回调] 用户已在黑名单: {user_id}")
            except Exception as e:
                logger.error(f"[回调] 拉黑用户失败: {e}")
                await query.message.edit_text(f"❌ 操作失败: {e}")
        elif action == "unblock":
            try:
                if remove_from_blacklist(bot_username, user_id):
                    await query.message.edit_text(f"✅ 已将用户 {user_id} 从黑名单移除")
                    logger.info(f"[回调] 成功解除拉黑: {user_id} (Bot: @{bot_username})")
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    # 获取用户信息
                    try:
                        user = await context.bot.get_chat(user_id)
                        user_username = user.username
                        user_name = user.full_name or "匿名用户"
                        # 优先使用 @用户名
                        if user_username:
                            user_display = f"@{user_username}"
                        else:
                            user_display = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
                        log_text = f"✅ Bot @{bot_username} 解除拉黑用户 {user_display} (ID: <code>{user_id}</code>) · {now}"
                    except:
                        # 如果获取失败，仅显示ID
                        log_text = f"✅ Bot @{bot_username} 解除拉黑用户 ID: <code>{user_id}</code> · {now}"
                    await send_admin_log(log_text)
                else:
                    await query.message.edit_text(f"⚠️ 用户 {user_id} 不在黑名单中")
                    logger.info(f"[回调] 用户不在黑名单: {user_id}")
            except Exception as e:
                logger.error(f"[回调] 解除拉黑失败: {e}")
                await query.message.edit_text(f"❌ 操作失败: {e}")
        else:  # unverify
            try:
                if remove_verified_user(bot_username, user_id):
                    await query.message.edit_text(f"🔓 已取消用户 {user_id} 的验证\n下次发送消息时需要重新验证")
                    logger.info(f"[回调] 成功取消验证: {user_id} (Bot: @{bot_username})")
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    # 获取用户信息
                    try:
                        user = await context.bot.get_chat(user_id)
                        user_username = user.username
                        user_name = user.full_name or "匿名用户"
                        # 优先使用 @用户名
                        if user_username:
                            user_display = f"@{user_username}"
                        else:
                            user_display = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
                        log_text = f"🔓 Bot @{bot_username} 取消用户 {user_display} (ID: <code>{user_id}</code>) 验证 · {now}"
                    except:
                        # 如果获取失败，仅显示ID
                        log_text = f"🔓 Bot @{bot_username} 取消用户 ID: <code>{user_id}</code> 验证 · {now}"
                    await send_admin_log(log_text)
                else:
                    await query.message.edit_text(f"⚠️ 用户 {user_id} 未验证或不存在")
                    logger.info(f"[回调] 用户未验证: {user_id}")
            except Exception as e:
                logger.error(f"[回调] 取消验证失败: {e}")
                await query.message.edit_text(f"❌ 操作失败: {e}")
        return

    if data == "addbot":
        await query.message.reply_text("㊙️ 请输入要添加的 Bot Token：")
        context.user_data["waiting_token"] = True
        return

    if data == "mybots":
        owner_id = str(query.from_user.id)
        bots = bots_data.get(owner_id, {}).get("bots", [])
        if not bots:
            await reply_and_auto_delete(query.message, "⚠️ 你还没有绑定任何 Bot。", delay=10)
            return

        keyboard = [
            [InlineKeyboardButton(f"@{b['bot_username']}", callback_data=f"info_{b['bot_username']}")]
            for b in bots
        ]
        keyboard.append([InlineKeyboardButton("🔙 返回", callback_data="back_home")])
        await query.message.edit_text("📋 你的 Bot 列表：", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "back_home":
        user_id = query.from_user.id
        await query.message.edit_text("📣 欢迎使用客服机器人管理面板\n👇 请选择操作：", reply_markup=manager_main_menu(user_id))
        return

    if data.startswith("info_"):
        bot_username = data.split("_", 1)[1]
        owner_id = str(query.from_user.id)

        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await reply_and_auto_delete(query.message, "⚠️ 找不到这个 Bot。", delay=10)
            return

        mode_label = "私聊" if target_bot.get("mode", "direct") == "direct" else "话题"
        forum_gid = target_bot.get("forum_group_id")
        blocked_count = db.get_blacklist_count(bot_username)  # 从数据库获取黑名单数量
        
        # 获取主人的用户名
        try:
            owner_user = await context.bot.get_chat(int(owner_id))
            owner_display = f"@{owner_user.username}" if owner_user.username else owner_user.full_name or "未知"
        except:
            owner_display = "未知"
        
        # 从数据库获取创建时间
        bot_info_db = db.get_bot(bot_username)
        created_at = bot_info_db.get('created_at', '未知') if bot_info_db else '未知'
        if created_at != '未知' and len(created_at) > 16:
            # 格式化时间显示（去掉秒数）
            created_at = created_at[:16]
        
        info_text = (
            f"🤖 Bot: @{bot_username}\n"
            f"🔑 Token: {target_bot['token'][:10]}... （已隐藏）\n"
            f"👤 绑定用户: {owner_display}\n"
            f"🆔 用户ID: {owner_id}\n"
            f"⏰ 创建时间: {created_at}\n"
            f"📡 当前模式: {mode_label} 模式\n"
            f"🏷 群ID: {forum_gid if forum_gid else '未设置'}\n"
            f"🚫 黑名单: {blocked_count} 个用户"
        )

        keyboard = [
            [InlineKeyboardButton("✏️ 设置欢迎语", callback_data=f"set_welcome_{bot_username}")],
            [InlineKeyboardButton("👁️ 预览欢迎语", callback_data=f"preview_welcome_{bot_username}")],
            [InlineKeyboardButton("🔐 验证设置", callback_data=f"verify_settings_{bot_username}")],
            [InlineKeyboardButton("🛠 话题群ID", callback_data=f"setforum_{bot_username}")],
            [InlineKeyboardButton("🔁 私聊模式", callback_data=f"mode_direct_{bot_username}")],
            [InlineKeyboardButton("🔁 话题模式", callback_data=f"mode_forum_{bot_username}")],
            [InlineKeyboardButton("❌ 断开连接", callback_data=f"del_{bot_username}")],
            [InlineKeyboardButton("🔙 返回", callback_data="mybots")]
        ]

        await query.message.edit_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("mode_direct_") or data.startswith("mode_forum_"):
        owner_id = str(query.from_user.id)
        _, mode, bot_username = data.split("_", 2)  # mode is 'direct' or 'forum'
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await reply_and_auto_delete(query.message, "⚠️ 找不到这个 Bot。", delay=10)
            return

        # ✅ 如果切换到话题模式但未设置群ID，直接拦截
        if mode == "forum" and not target_bot.get("forum_group_id"):
            await reply_and_auto_delete(
                query.message,
                "⚠️ 请先\"🛠 设置 话题群ID\"。",
                delay=10
            )
            return

        # 检查是否已经是当前模式
        current_mode = target_bot.get("mode", "direct")
        if current_mode == mode:
            mode_cn = "私聊模式" if mode == "direct" else "话题模式"
            await query.message.reply_text(f"ℹ️ @{bot_username} 当前已经是 {mode_cn}，无需切换。")
            return

        target_bot["mode"] = mode
        
        # 💾 保存到数据库
        db.update_bot_mode(bot_username, mode)
        save_bots()

        # 显示中文标签 & 推送到 ADMIN_CHANNEL
        mode_cn_full = "私聊模式" if mode == "direct" else "话题模式"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        user_username = query.from_user.username
        user_display = f"@{user_username}" if user_username else f"用户ID: {owner_id}"
        await send_admin_log(f"📡 {user_display} (ID: <code>{owner_id}</code>) 将 @{bot_username} 切换为 {mode_cn_full} · {now}")

        await query.message.reply_text(f"✅ 已将 @{bot_username} 切换为 {mode_cn_full.split('模式')[0]} 模式。")


    # ================== 验证设置管理 ==================
    
    # 验证设置菜单
    if data.startswith("verify_settings_"):
        bot_username = data.split("_", 2)[2]
        owner_id = str(query.from_user.id)
        
        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await reply_and_auto_delete(query.message, "⚠️ 找不到这个 Bot。", delay=10)
            return
        
        # 获取当前验证类型
        current_type = target_bot.get('verification_type', 'simple')
        
        info_text, keyboard = build_verify_settings(bot_username, current_type)
        
        await query.message.edit_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # 切换到简单验证
    if data.startswith("verify_simple_"):
        bot_username = data.split("_", 2)[2]
        owner_id = str(query.from_user.id)
        
        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await query.answer("⚠️ 找不到这个 Bot", show_alert=True)
            return
        
        # 检查是否已经是简单验证
        current_type = target_bot.get('verification_type', 'simple')
        if current_type == 'simple':
            await query.answer("ℹ️ 当前已经是简单验证码模式", show_alert=False)
            return

        
        # 更新数据库
        db.update_bot_verification_type(bot_username, 'simple')
        # 更新内存
        target_bot['verification_type'] = 'simple'
        
        await query.answer("✅ 已切换到简单验证码", show_alert=True)
        
        # 刷新菜单显示（更新勾选状态）
        info_text, keyboard = build_verify_settings(bot_username, 'simple')
        await query.edit_message_text(
            text=info_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    
    # 切换到 CF 验证
    if data.startswith("verify_cf_"):
        bot_username = data.split("_", 2)[2]
        owner_id = str(query.from_user.id)
        
        # 鉴权
        if str(query.from_user.id) != owner_id:
            await query.answer("⚠️ 你没有权限管理这个 Bot", show_alert=True)
            return

        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await query.answer("⚠️ 找不到这个 Bot", show_alert=True)
            return
        
        # 检查是否已经是 CF 验证
        current_type = target_bot.get('verification_type', 'simple')
        if current_type == 'cf':
            await query.answer("ℹ️ 当前已经是 Cloudflare 验证模式", show_alert=False)
            return

        
        # 更新数据库
        db.update_bot_verification_type(bot_username, 'cf')
        # 更新内存
        target_bot['verification_type'] = 'cf'
        
        await query.answer("✅ 已切换到 Cloudflare 验证", show_alert=True)
        
        # 刷新菜单显示（更新勾选状态）
        info_text, keyboard = build_verify_settings(bot_username, 'cf')
        await query.edit_message_text(
            text=info_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # 切换到人工验证
    if data.startswith("verify_manual_"):
        bot_username = data.split("_", 2)[2]
        owner_id = str(query.from_user.id)
        
        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await query.answer("⚠️ 找不到这个 Bot", show_alert=True)
            return
        
        # 检查是否已经是人工验证
        current_type = target_bot.get('verification_type', 'simple')
        if current_type == 'manual':
            await query.answer("ℹ️ 当前已经是人工验证模式", show_alert=False)
            return
        
        # 更新数据库
        db.update_bot_verification_type(bot_username, 'manual')
        # 更新内存
        target_bot['verification_type'] = 'manual'
        
        await query.answer("✅ 已切换到人工验证", show_alert=True)
        
        # 刷新菜单显示（更新勾选状态）
        info_text, keyboard = build_verify_settings(bot_username, 'manual')
        await query.edit_message_text(
            text=info_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # 切换到自动拒绝所有验证
    if data.startswith("verify_deny_"):
        bot_username = data.split("_", 2)[2]
        owner_id = str(query.from_user.id)
        
        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await query.answer("⚠️ 找不到这个 Bot", show_alert=True)
            return
        
        # 检查是否已经是自动拒绝
        current_type = target_bot.get('verification_type', 'simple')
        if current_type == 'deny':
            await query.answer("ℹ️ 当前已经是自动拒绝模式", show_alert=False)
            return
        
        # 更新数据库
        db.update_bot_verification_type(bot_username, 'deny')
        # 更新内存
        target_bot['verification_type'] = 'deny'
        
        await query.answer("✅ 已切换到自动拒绝所有验证", show_alert=True)
        
        # 刷新菜单显示（更新勾选状态）
        info_text, keyboard = build_verify_settings(bot_username, 'deny')
        await query.edit_message_text(
            text=info_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # 切换到取消验证
    if data.startswith("verify_none_"):
        bot_username = data.split("_", 2)[2]
        owner_id = str(query.from_user.id)
        
        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await query.answer("⚠️ 找不到这个 Bot", show_alert=True)
            return
        
        # 检查是否已经是取消验证
        current_type = target_bot.get('verification_type', 'simple')
        if current_type == 'none':
            await query.answer("ℹ️ 当前已经是取消验证模式", show_alert=False)
            return
        
        # 更新数据库
        db.update_bot_verification_type(bot_username, 'none')
        # 更新内存
        target_bot['verification_type'] = 'none'
        
        await query.answer("✅ 已切换到取消验证", show_alert=True)
        
        # 刷新菜单显示（更新勾选状态）
        info_text, keyboard = build_verify_settings(bot_username, 'none')
        await query.edit_message_text(
            text=info_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ================== 人工验证审核 ==================
    if data.startswith("manual_verify_approve:") or data.startswith("manual_verify_reject:"):
        is_approve = data.startswith("manual_verify_approve:")
        payload = data.split(":", 1)[1]
        bot_username, user_id_str = payload.rsplit("|", 1)
        user_id = int(user_id_str)
        
        # 只有该 Bot 的拥有者可以审核
        owner_id = get_bot_owner(bot_username)
        if query.from_user.id != owner_id:
            await query.answer("⚠️ 只有该 Bot 的管理员可以审核", show_alert=True)
            return
        
        pending = db.get_pending_manual_verification(bot_username, user_id)
        if not pending:
            await query.answer("⚠️ 该申请已处理或不存在", show_alert=True)
            return
        
        user_name = pending.get('user_name') or ''
        user_username = pending.get('user_username') or ''
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        username_line = f"📱 用户名: @{user_username}\n" if user_username else ""
        
        if is_approve:
            # 通过：加入已验证用户
            add_verified_user(bot_username, user_id, user_name, user_username)
            db.resolve_manual_verification(bot_username, user_id, 'approved')
            # 清理可能残留的验证码待验证记录
            db.remove_pending_verification(bot_username, user_id)
            pending_verifications.pop(f"{bot_username}_{user_id}", None)
            
            # 通知用户并通过欢迎语
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ <b>人工验证已通过</b>\n\n欢迎使用本机器人！",
                    parse_mode="HTML"
                )
                welcome_msg = get_welcome_message(bot_username)
                if welcome_msg:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=welcome_msg,
                        parse_mode="HTML"
                    )
            except Exception as e:
                logger.error(f"通知用户验证通过失败: {e}")
            
            review_result = (
                f"✅ <b>人工验证已通过</b> (@{bot_username})\n\n"
                f"👤 昵称: {user_name}\n"
                f"{username_line}"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"⏰ 处理时间: {now}"
            )
            await query.edit_message_text(review_result, parse_mode="HTML")
            await query.answer("✅ 已通过该用户的验证", show_alert=False)
        else:
            db.resolve_manual_verification(bot_username, user_id, 'rejected')
            db.remove_pending_verification(bot_username, user_id)
            pending_verifications.pop(f"{bot_username}_{user_id}", None)
            
            # 通知用户
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ <b>人工验证未通过</b>\n\n很抱歉，你暂时无法使用本机器人。",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"通知用户验证未通过失败: {e}")
            
            review_result = (
                f"❌ <b>人工验证已拒绝</b> (@{bot_username})\n\n"
                f"👤 昵称: {user_name}\n"
                f"{username_line}"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"⏰ 处理时间: {now}"
            )
            await query.edit_message_text(review_result, parse_mode="HTML")
            await query.answer("❌ 已拒绝该用户的验证", show_alert=False)
        return

    if data.startswith("setforum_"):

        bot_username = data.split("_", 1)[1]
        context.user_data["waiting_forum_for"] = {"bot_username": bot_username}
        await query.message.reply_text(
            f"💣 请先将 Bot 拉入话题群，给管理员权限\n\n"
            f"㊙️ 请输入话题群 ID（给 @{bot_username} 使用）：\n\n"
            f"⚠️ 注意事项：\n"
            f"• 正确格式：-1 开头的 13 位数字\n"
            f"• 示例：-1004877845787\n"
            f"• 请在群组设置页面获取群ID\n"
            f"• 话题模式下 500 开头的话题ID无效"
        )
        return

    # ================== 欢迎语管理 ==================
    
    # 预览欢迎语
    if data.startswith("preview_welcome_"):
        bot_username = data.split("_", 2)[2]
        owner_id = str(query.from_user.id)
        
        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await reply_and_auto_delete(query.message, "⚠️ 找不到这个 Bot。", delay=10)
            return
        
        # 获取当前生效的欢迎语
        welcome_msg = get_welcome_message(bot_username)
        
        # 判断来源
        bot_info = db.get_bot(bot_username)
        if bot_info and bot_info.get('welcome_msg'):
            source = "✏️ 自定义欢迎语"
        elif db.get_global_welcome():
            source = "🌐 管理员全局欢迎语"
        else:
            source = "📝 系统默认欢迎语"
        
        preview_text = (
            f"👁️ 欢迎语预览 (@{bot_username})\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"{source}\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"{welcome_msg}\n\n"
            f"━━━━━━━━━━━━━━"
        )
        
        keyboard = [
            [InlineKeyboardButton("✏️ 修改欢迎语", callback_data=f"set_welcome_{bot_username}")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"info_{bot_username}")]
        ]
        
        await query.message.edit_text(preview_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # 设置欢迎语
    if data.startswith("set_welcome_"):
        bot_username = data.split("_", 2)[2]
        owner_id = str(query.from_user.id)
        
        # 验证权限
        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await reply_and_auto_delete(query.message, "⚠️ 找不到这个 Bot。", delay=10)
            return
        
        # 设置状态，等待用户输入
        context.user_data["action"] = "set_welcome"
        context.user_data["bot_username"] = bot_username
        
        # 获取当前欢迎语
        bot_info = db.get_bot(bot_username)
        current_welcome = bot_info.get('welcome_msg', '') if bot_info else ''
        
        tip_text = (
            f"✏️ 设置欢迎语 (@{bot_username})\n\n"
            f"请输入新的欢迎语内容：\n\n"
            f"💡 提示：\n"
            f"• 支持多行文本\n"
            f"• 可以使用 Emoji 表情\n"
            f"• 发送 /cancel 取消设置\n"
            f"• 发送 /clear 清除自定义欢迎语（恢复为全局/默认）\n\n"
        )
        
        if current_welcome:
            tip_text += f"━━━━━━━━━━━━━━\n当前自定义欢迎语：\n{current_welcome[:100]}{'...' if len(current_welcome) > 100 else ''}"
        
        await query.message.edit_text(tip_text)
        return
    
    # 管理员全局欢迎语
    if data == "admin_global_welcome":
        if not is_admin(query.from_user.id):
            await reply_and_auto_delete(query.message, "⚠️ 无权限访问", delay=5)
            return
        
        global_welcome = db.get_global_welcome()
        
        if global_welcome:
            text = (
                f"📝 全局欢迎语设置\n\n"
                f"━━━━━━━━━━━━━━\n"
                f"当前全局欢迎语：\n\n"
                f"{global_welcome[:200]}{'...' if len(global_welcome) > 200 else ''}\n"
                f"━━━━━━━━━━━━━━\n\n"
                f"💡 说明：全局欢迎语会应用于所有未自定义欢迎语的机器人"
            )
            keyboard = [
                [InlineKeyboardButton("✏️ 修改", callback_data="admin_edit_global_welcome")],
                [InlineKeyboardButton("🗑️ 清除", callback_data="admin_clear_global_welcome")],
                [InlineKeyboardButton("🔙 返回", callback_data="back_home")]
            ]
        else:
            text = (
                f"📝 全局欢迎语设置\n\n"
                f"⚠️ 尚未设置全局欢迎语\n\n"
                f"💡 说明：设置后，所有未自定义欢迎语的机器人将使用全局欢迎语"
            )
            keyboard = [
                [InlineKeyboardButton("➕ 设置全局欢迎语", callback_data="admin_edit_global_welcome")],
                [InlineKeyboardButton("🔙 返回", callback_data="back_home")]
            ]
        
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # 管理员编辑全局欢迎语
    if data == "admin_edit_global_welcome":
        if not is_admin(query.from_user.id):
            await reply_and_auto_delete(query.message, "⚠️ 无权限访问", delay=5)
            return
        
        context.user_data["action"] = "set_global_welcome"
        
        global_welcome = db.get_global_welcome()
        tip_text = (
            f"✏️ 设置全局欢迎语\n\n"
            f"请输入全局欢迎语内容：\n\n"
            f"💡 提示：\n"
            f"• 支持多行文本\n"
            f"• 可以使用 Emoji 表情\n"
            f"• 发送 /cancel 取消设置\n"
            f"• 全局欢迎语仅对未自定义的机器人生效\n\n"
        )
        
        if global_welcome:
            tip_text += f"━━━━━━━━━━━━━━\n当前全局欢迎语：\n{global_welcome[:100]}{'...' if len(global_welcome) > 100 else ''}"
        
        await query.message.edit_text(tip_text)
        return
    
    # 管理员清除全局欢迎语
    if data == "admin_clear_global_welcome":
        if not is_admin(query.from_user.id):
            await reply_and_auto_delete(query.message, "⚠️ 无权限访问", delay=5)
            return
        
        if db.delete_global_welcome():
            await query.message.edit_text(
                "✅ 已清除全局欢迎语\n\n所有机器人将使用系统默认欢迎语（除非已自定义）",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]])
            )
        else:
            await query.message.edit_text(
                "⚠️ 清除失败或全局欢迎语不存在",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_home")]])
            )
        return

    if data.startswith("del_"):
        bot_username = data.split("_", 1)[1]
        owner_id = str(query.from_user.id)
        owner_username = query.from_user.username or ""

        bots = bots_data.get(owner_id, {}).get("bots", [])
        target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
        if not target_bot:
            await reply_and_auto_delete(query.message, "⚠️ 找不到这个 Bot。", delay=10)
            return

        try:
            if bot_username in running_apps:
                app = running_apps.pop(bot_username)
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            bots.remove(target_bot)
            
            # 💾 从数据库删除
            db.delete_bot(bot_username)
            save_bots()
            
            # 🔄 触发静默备份（不推送通知）
            trigger_backup(silent=True)
            
            await query.message.edit_text(f"✅ 已断开Bot：@{bot_username}")

            # 🔔 删除通知（发送到管理频道）
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            # 优先使用 @用户名
            user_display = f"@{owner_username}" if owner_username else f"用户ID: {owner_id}"
            log_text = (
                f"🗑 {user_display}\n"
                f"🆔 <code>{owner_id}</code>\n"
                f"🤖 Bot: @{bot_username}\n"
                f"⏰ {now}"
            )
            await send_admin_log(log_text)
        except Exception as e:
            await reply_and_auto_delete(query.message, f"❌ 删除失败: {e}", delay=10)
        return

# ================== 主入口 ==================
async def run_all_bots():
    if not MANAGER_TOKEN:
        logger.error("MANAGER_TOKEN 未设置，无法启动管理Bot。")
        return

    # 初始化数据库
    db.init_database()
    
    # 从数据库加载配置
    load_bots()
    load_map()

    # 启动子 bot（恢复）
    for owner_id, info in bots_data.items():
        for b in info.get("bots", []):
            token = b["token"]; bot_username = b["bot_username"]
            try:
                app = Application.builder().token(token).build()
                app.add_handler(CommandHandler("start", subbot_start))
                # 处理普通消息
                app.add_handler(MessageHandler(filters.ALL, partial(handle_message, owner_id=int(owner_id), bot_username=bot_username)))
                # 处理编辑消息 - 使用 filters.UpdateType.EDITED_MESSAGE
                app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, partial(handle_message, owner_id=int(owner_id), bot_username=bot_username)))
                # 💡 添加回调处理器（处理 /id 命令的按钮）
                app.add_handler(CallbackQueryHandler(callback_handler))
                running_apps[bot_username] = app
                await app.initialize()
                await app.start()
                
                # 设置子机器人的命令菜单（仅对绑定用户显示）
                try:
                    # 先清除所有默认命令（全局）
                    await app.bot.delete_my_commands()
                    logger.info(f"✅ 已清除 @{bot_username} 的全局命令菜单")
                    
                    # 尝试为 owner 设置命令菜单（如果bot和owner还没对话会失败，这是正常的）
                    try:
                        commands = [
                            BotCommand("start", "开始使用"),
                            BotCommand("id", "查看用户"),
                            BotCommand("b", "拉黑用户"),
                            BotCommand("ub", "解除拉黑"),
                            BotCommand("bl", "查看黑名单"),
                            BotCommand("uv", "取消用户验证")
                        ]
                        await app.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=int(owner_id)))
                        logger.info(f"✅ 已为 @{bot_username} 的拥有者（ID: {owner_id}）设置专属命令菜单")
                    except Exception as scope_err:
                        # Bot还没和owner对话过，等用户首次/start后会自动设置
                        logger.info(f"ℹ️  @{bot_username} 暂未与拥有者建立对话，将在首次对话时设置命令菜单")
                except Exception as cmd_err:
                    logger.error(f"❌ 设置命令菜单失败 @{bot_username}: {cmd_err}")
                
                await app.updater.start_polling()
                logger.info(f"启动子Bot: @{bot_username}")
            except Exception as e:
                logger.error(f"子Bot启动失败: @{bot_username} {e}")

    # 管理 Bot
    manager_app = Application.builder().token(MANAGER_TOKEN).build()
    manager_app.add_handler(CommandHandler("start", manager_start))
    # 添加欢迎语设置相关的命令处理器
    async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理/cancel命令"""
        action = context.user_data.get("action")
        if action in ["set_welcome", "set_global_welcome"]:
            context.user_data.pop("action", None)
            context.user_data.pop("bot_username", None)
            await update.message.reply_text("❌ 已取消设置")
        else:
            await update.message.reply_text("⚠️ 当前没有进行中的设置操作")
    
    async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理/clear命令"""
        action = context.user_data.get("action")
        if action == "set_welcome":
            bot_username = context.user_data.get("bot_username")
            owner_id = str(update.message.chat.id)
            
            # 验证权限
            bots = bots_data.get(owner_id, {}).get("bots", [])
            target_bot = next((b for b in bots if b["bot_username"] == bot_username), None)
            if not target_bot:
                await update.message.reply_text("⚠️ 找不到这个 Bot")
                context.user_data.pop("action", None)
                context.user_data.pop("bot_username", None)
                return
            
            # 清除自定义欢迎语
            if db.update_bot_welcome(bot_username, ""):
                # 更新内存
                target_bot["welcome_msg"] = ""
                load_bots()
                await update.message.reply_text(
                    f"✅ 已清除 @{bot_username} 的自定义欢迎语\n\n"
                    f"现在将使用{'管理员全局欢迎语' if db.get_global_welcome() else '系统默认欢迎语'}"
                )
            else:
                await update.message.reply_text("❌ 清除失败")
            
            context.user_data.pop("action", None)
            context.user_data.pop("bot_username", None)
        else:
            await update.message.reply_text("⚠️ 请先进入欢迎语设置模式")
    
    manager_app.add_handler(CommandHandler("cancel", handle_cancel))
    manager_app.add_handler(CommandHandler("clear", handle_clear))
    manager_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, token_listener))
    manager_app.add_handler(CallbackQueryHandler(callback_handler))
    running_apps["__manager__"] = manager_app

    await manager_app.initialize(); await manager_app.start(); await manager_app.updater.start_polling()
    logger.info("管理 Bot 已启动 ✅")
    if ADMIN_CHANNEL:
        try:
            await manager_app.bot.send_message(ADMIN_CHANNEL, "✅ 宿主管理Bot已启动")
        except Exception as e:
            logger.error(f"启动通知失败: {e}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(run_all_bots())
