from aiogram.fsm.state import State, StatesGroup


class AdminState(StatesGroup):
    find_user = State()
    grant_user_id = State()
    grant_days = State()
    broadcast = State()
    payinvoice = State()
    wait_for_balance_amount = State()
    wait_for_gift_days = State()
    ban_user = State()
    ban_vpn_user = State()


class AdminPaymentState(StatesGroup):
    wait_for_crypto_token = State()
    wait_for_yoomoney_wallet = State()
    wait_for_yoomoney_secret = State()
    wait_for_lava_creds = State()
    wait_for_tome_phone = State()
    wait_for_tome_bank = State()


class AdminInvoiceState(StatesGroup):
    wait_for_search = State()


class AdminSettingsState(StatesGroup):
    wait_for_ref_start = State()
    wait_for_ref_lvl1 = State()
    wait_for_ref_lvl2 = State()
    wait_for_admin_id = State()
    wait_for_plan_id = State()
    wait_for_plan_title = State()
    wait_for_plan_days = State()
    wait_for_plan_price = State()
    wait_for_promo_code = State()
    wait_for_promo_type = State()
    wait_for_promo_value = State()
    wait_for_promo_uses = State()
    wait_for_test_days = State()
    wait_for_limit_gb = State()
    wait_for_payment_channel = State()
    wait_for_main_channel = State()
    wait_for_usdt_rate = State()


class UserState(StatesGroup):
    wait_for_promo = State()
    wait_for_gift_email = State()
    wait_for_support = State()
    wait_for_yoomoney_sender_name = State()


class SupportState(StatesGroup):
    wait_for_reply = State()


class WithdrawalState(StatesGroup):
    wait_for_details = State()
