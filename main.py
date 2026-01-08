import asyncio
import logging
import sqlite3
import sys

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session import aiohttp
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
import os
from aiogram.types import InputMediaPhoto, FSInputFile
from tempfile import NamedTemporaryFile
from aiogram.types import InputMediaPhoto, InputFile
from sqlalchemy import select
from aiogram.filters import BaseFilter, Command, CommandStart, or_f
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ChatMemberAdministrator,
    ChatMemberOwner,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo
)
from aiogram.utils.i18n import FSMI18nMiddleware, I18n
from aiogram.utils.i18n import gettext as _
from aiogram.utils.i18n import lazy_gettext as __
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
import time
from database.models import User
from database.engine import session_maker
from admin.callbacks.inline_factory import (
    ActionsUpDe,
    CityCbUpDe,
    CityPagination,
    CountryCbUpDe,
    CountryPagination,
    DistrictCbUpDe,
    DistrictChangeLocation,
    DistrictChangeName,
    DistrictChangeNumber,
    DistrictChangeNumbers,
    DistrictPagination,
)
from aiogram.types import InputMediaPhoto
from admin.keyboards.base_keybord_builder import base_kb_builder
from admin.keyboards.inline_keyboard_builder import (
    Paginator,
    change_district,
    choise_district_kb,
    choise_location_kb,
    confirm_delete,
    list_location_kb,
    numbers_kb,
)
from admin.states.change_objects import ChangeObject
from admin.states.create_contact_states import (
    ChoicesKeyboardAddContact,
    CreateContactState,
    TariffAdminState,
    admin_alert,
    admin_example,
)
from database.check_user import Request
from database.engine import create_db, session_maker
from database.middlewares import DataBaseSession
from database.orm_query import (
    get_all_cities,
    get_all_countryes,
    get_all_districts,
    get_all_tariff_categories,
    get_all_tariff_countries,
    get_city_by_id,
    get_country_by_id,
    get_district_by_id,
    get_language,
    get_tariff_from_countries,
    get_tariff_to_countries,
    get_tariff_by_id,
    get_tariffs_for_route,
    get_numbers_by_district_id,
    orm_add_tariff,
    orm_add_tariff_category,
    orm_add_tariff_country,
    orm_add_city,
    orm_add_country,
    orm_add_district_names,
    orm_add_number,
    orm_add_user,
    orm_delete_city,
    orm_delete_country,
    orm_delete_district,
    orm_update_city,
    orm_update_country,
    orm_update_district_location,
    orm_update_district_names,
    orm_update_number,
)

# TOKEN = "6894626851:AAG5NkFOdBRWNZsRj2cENWkmTsC_r0y5LLA"
TOKEN = "8396669139:AAFvr8gWi7uXDMwPLBePF9NmYf16wsHmtPU"

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


user_list = [574853103, 506687945, 960217500, 688971244]
group_id = -1001719052220
# dp = Dispatcher(storage=RedisStorage())


def is_admin_user(user_id: int) -> bool:
    return user_id in user_list


# Подключение к базе данных SQLite
conn = sqlite3.connect("users.db")
cursor = conn.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)""")
conn.commit()


# Определяем состояния
class BroadcastState(StatesGroup):
    waiting_for_message = State()
    waiting_confirmation = State()
    broadcasting = State()


class Menu(StatesGroup):
    choose_language = State()
    main_menu = State()
    check_id = State()
    send_msg = State()
    send_all = State()


class TariffUserState(StatesGroup):
    select_from_country = State()
    select_to_country = State()


# Храним список админов
admins_cache = set()
line = "⠀" * 25


@dp.message(Command("update_admins"), F.chat.type.in_(["group", "supergroup"]))
async def update_admins(message: types.Message):
    global admins_cache  # Используем глобальную переменную

    chat_id = message.chat.id

    # Получаем список админов
    admins = await bot.get_chat_administrators(chat_id)

    # Сохраняем ID админов в кеш
    admins_cache = {
        member.user.id
        for member in admins
        if isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))
    }

    # Удаляем сообщение, если отправитель — админ
    if message.from_user.id in admins_cache:
        await message.delete()


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in admins_cache


# админка
@dp.message(F.text == __("👤 Контакты"), IsAdmin(), F.chat.type == "private")
async def visualize_kb(message: Message, state: FSMContext):
    await state.clear()
    text = "Админка запущена!"
    await message.answer(
        text, reply_markup=base_kb_builder(ChoicesKeyboardAddContact, 1)
    )


@dp.message(F.text == ChoicesKeyboardAddContact.manage_tariffs, IsAdmin())
async def manage_tariffs(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(
        text="➕ Добавить страну отправителя/получателя",
        callback_data=TariffAdminActionCallback(action="add_country").pack(),
    )
    kb.button(
        text="➕ Добавить категорию тарифа",
        callback_data=TariffAdminActionCallback(action="add_category").pack(),
    )
    kb.button(
        text="➕ Добавить тариф",
        callback_data=TariffAdminActionCallback(action="add_tariff").pack(),
    )
    kb.adjust(1)
    await message.answer("Управление тарифами:", reply_markup=kb.as_markup())


semaphore = asyncio.Semaphore(25)


async def send_broadcast(
    session,
    admin_id,
    message="",
    state=None,
    content_type="text",
    photo=None,
    photo_caption=None,
):
    """Улучшенная рассылка с асинхронной обработкой и ограничением скорости"""
    if state:
        await state.clear()

    request = Request(session)
    users = await request.get_active_users()

    # Статистика выполнения
    stats = {"sent": 0, "blocked": 0, "failed": 0, "total": len(users)}
    progress_msg_id = None
    last_update_time = time.time()

    # Функция для обновления прогресса рассылки
    async def update_progress():
        nonlocal progress_msg_id, last_update_time
        current_time = time.time()

        # Обновляем статус не чаще раза в 5 секунд
        if current_time - last_update_time < 5:
            return

        last_update_time = current_time
        completed = stats["sent"] + stats["blocked"] + stats["failed"]
        progress_text = (
            f"📤 <b>Рассылка в процессе:</b> {completed}/{stats['total']}\n\n"
            f"✅ Успешно: {stats['sent']}\n"
            f"🚫 Заблокировали: {stats['blocked']}\n"
            f"⚠️ Ошибки: {stats['failed']}"
        )

        try:
            if progress_msg_id:
                await bot.edit_message_text(
                    progress_text,
                    chat_id=admin_id,
                    message_id=progress_msg_id,
                    parse_mode="HTML",
                )
            else:
                msg = await bot.send_message(admin_id, progress_text, parse_mode="HTML")
                progress_msg_id = msg.message_id
        except Exception:
            pass  # Игнорируем ошибки обновления прогресса

    async def send_to_user(user_id):
        retries = 0
        max_retries = 3
        backoff_factor = 1.5  # Экспоненциальное увеличение задержки

        while retries <= max_retries:
            try:
                async with semaphore:  # Ограничиваем количество одновременных запросов
                    if content_type == "photo" and photo:
                        await bot.send_photo(
                            user_id,
                            photo=photo,
                            caption=photo_caption or "",
                            parse_mode="HTML",
                        )
                    else:
                        await bot.send_message(user_id, message, parse_mode="HTML")

                    stats["sent"] += 1
                    await update_progress()
                    return

            except TelegramForbiddenError:
                await request.disable_user(user_id)
                stats["blocked"] += 1
                await update_progress()
                return

            except TelegramRetryAfter as e:
                # При ошибке флуда соблюдаем обязательную паузу
                await asyncio.sleep(e.retry_after)
                retries += 1

            except Exception:
                retries += 1
                # Используем экспоненциальную задержку между попытками
                await asyncio.sleep(0.5 * (backoff_factor**retries))

        # Все попытки неудачны
        stats["failed"] += 1
        await update_progress()

    # Отправляем начальное сообщение о запуске рассылки
    status_msg = await bot.send_message(
        admin_id,
        f"📢 <b>Рассылка запущена</b>\n\nВсего получателей: {len(users)}",
        parse_mode="HTML",
    )

    # Создаем группы по 100 пользователей для последовательной обработки
    batch_size = 100
    user_batches = [users[i : i + batch_size] for i in range(0, len(users), batch_size)]

    # Обрабатываем группы последовательно, а пользователей в группе - параллельно
    for batch in user_batches:
        tasks = [send_to_user(user_id) for user_id in batch]
        await asyncio.gather(*tasks)

    # Формируем итоговый отчет
    final_report = (
        f"📢 <b>Рассылка завершена!</b>\n\n"
        f"✅ Успешно отправлено: {stats['sent']}\n"
        f"🚫 Пользователи заблокировали бота: {stats['blocked']}\n"
        f"⚠️ Ошибки отправки: {stats['failed']}\n\n"
        f"Всего получателей: {stats['total']}"
    )

    # Пытаемся обновить существующее сообщение статуса
    try:
        if progress_msg_id:
            await bot.edit_message_text(
                final_report,
                chat_id=admin_id,
                message_id=progress_msg_id,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(admin_id, final_report, parse_mode="HTML")
    except Exception:
        # Если не получилось обновить, отправляем новое сообщение
        await bot.send_message(admin_id, final_report, parse_mode="HTML")

    return final_report


@dp.message(
    F.text == ChoicesKeyboardAddContact.send_news, IsAdmin(), F.chat.type == "private"
)
async def start_broadcast(message: Message, state: FSMContext):
    """Запуск процесса рассылки"""
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text="Отменить", callback_data="cancel_broadcast")
    )

    await message.answer(
        "<b>Введите сообщение для рассылки.</b>\nЕсли передумали нажмите отменить!",
        reply_markup=keyboard.as_markup(),
    )
    await state.set_state(BroadcastState.waiting_for_message)


@dp.callback_query(lambda c: c.data == "cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Отмена рассылки"""
    await state.clear()
    await callback.message.edit_text("❌ Рассылка отменена")
    await callback.answer()


@dp.message(BroadcastState.waiting_for_message)
async def process_broadcast(message: Message, session: AsyncSession, state: FSMContext):
    """Обработка сообщения и запуск фоновой рассылки"""
    admin_id = message.from_user.id

    # Сохраняем контент для рассылки в состояние
    await state.update_data(
        admin_id=admin_id,
        has_photo=bool(message.photo),
        photo_id=message.photo[-1].file_id if message.photo else None,
        caption=message.caption or "",
        text=message.text or "",
    )

    # Запрашиваем подтверждение
    data = await state.get_data()
    preview = data.get("text") or f"[Фото] {data.get('caption') or '(без подписи)'}"

    # Обрезаем превью если оно слишком длинное
    if len(preview) > 300:
        preview = preview[:297] + "..."

    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_broadcast"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_broadcast"),
    )

    await message.answer(
        f"<b>Предпросмотр сообщения:</b>\n\n{preview}\n\n"
        f"Отправить это сообщение всем пользователям?",
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML",
    )

    await state.set_state(BroadcastState.waiting_confirmation)


@dp.callback_query(
    lambda c: c.data == "confirm_broadcast", BroadcastState.waiting_confirmation
)
async def confirm_broadcast(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
):
    """Подтверждение и запуск рассылки"""
    data = await state.get_data()
    admin_id = data["admin_id"]

    # Запускаем рассылку в фоне
    task = asyncio.create_task(broadcast_task(data=data, session=session, state=state))

    # Сохраняем task в состояние для возможной отмены в будущем
    await state.update_data(broadcast_task=task)

    await callback.message.edit_text(
        "✅ Рассылка запущена!\nВы получите уведомления о ходе выполнения."
    )
    await callback.answer()

    # Очищаем состояние, но сохраняем информацию о задаче
    current_state = await state.get_state()
    await state.clear()
    await state.set_state(BroadcastState.broadcasting)


async def broadcast_task(data: dict, session: AsyncSession, state: FSMContext):
    """Фоновая задача для рассылки"""
    admin_id = data["admin_id"]
    has_photo = data["has_photo"]

    try:
        if has_photo:
            result = await send_broadcast(
                session=session,
                admin_id=admin_id,
                content_type="photo",
                photo=data["photo_id"],
                photo_caption=data["caption"],
            )
        else:
            result = await send_broadcast(
                session=session, admin_id=admin_id, message=data["text"]
            )

    except asyncio.CancelledError:
        await bot.send_message(admin_id, "❌ Рассылка была отменена")
        return

    except Exception as e:
        logging.error(f"Критическая ошибка рассылки: {e}", exc_info=True)
        await bot.send_message(
            admin_id, f"⚠️ Рассылка завершилась критической ошибкой:\n{str(e)[:200]}"
        )
    finally:
        await state.clear()


# main_admin
@dp.message(F.text == ChoicesKeyboardAddContact.add_contacts, IsAdmin())
async def start_up_de(message: Message):
    await show_country_list(message, 0)


# 1государства
async def show_country_list(message_or_callback: Message | CallbackQuery, page: int):
    async with session_maker() as session:
        countryes = await get_all_countryes(session)

    text_info = line + "\n" + "<b>Выберите государство:</b>"

    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(
            text_info,
            reply_markup=list_location_kb(
                countryes,
                CountryCbUpDe,
                CountryPageCallback,
                page,
                add_country=CountryCbUpDe(id=0, action=ActionsUpDe.add).pack(),
            ),
        )
    else:
        await message_or_callback.message.answer(
            text_info,
            reply_markup=list_location_kb(
                countryes,
                CountryCbUpDe,
                lambda page: CountryPageCallback(page=page),
                page,
                add_country=CountryCbUpDe(id=0, action=ActionsUpDe.add).pack(),
            ),
        )


@dp.callback_query(CountryCbUpDe.filter(F.action == ActionsUpDe.add))
async def add_country_cb(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.answer(
        text=f"Введите название государства\n{admin_example['help_create_country']}",
    )
    await callback_query.answer()
    await state.set_state(CreateContactState.country)


@dp.message(CreateContactState.country, F.text)
async def add_country_message(message: Message, state: FSMContext):
    try:
        country_ru, country_en, country_uz, position = message.text.split(",")
    except ValueError:
        await message.answer(
            text=f"{admin_alert['wrong']}{admin_example['help_create_country']}"
        )
        return

    async with session_maker() as session:
        await orm_add_country(
            session, country_ru, country_en, country_uz, int(position)
        )

    await state.clear()
    await message.answer("Государство добавлено  ✅")
    await show_country_list(message, 0)


@dp.callback_query(CountryCbUpDe.filter(F.action == ActionsUpDe.select_action))
async def chouse_country_dop(
    callback_query: CallbackQuery, callback_data: CountryCbUpDe
):
    text_info = "<b>Выберите действие!</b>"
    country_id = callback_data.id

    await callback_query.message.edit_text(
        text_info,
        reply_markup=choise_location_kb(
            name="городов",
            add_name="город",
            callbacks_lst=[
                CityPagination(id=country_id, page=0).pack(),
                CityCbUpDe(id=country_id, action=ActionsUpDe.add).pack(),
                CountryCbUpDe(id=country_id, action=ActionsUpDe.edit).pack(),
                CountryCbUpDe(id=country_id, action=ActionsUpDe.confirm_delete).pack(),
                CountryPagination(page=0).pack(),
            ],
        ),
    )


@dp.callback_query(CityCbUpDe.filter(F.action == ActionsUpDe.add))
async def add_city_cb(
    callback_query: CallbackQuery, callback_data: CityCbUpDe, state: FSMContext
):
    await state.update_data(country_id=callback_data.id)
    await callback_query.message.answer(
        text=f"Введите название города\n{admin_example['help_create_city']}",
    )
    await callback_query.answer()
    await state.set_state(CreateContactState.city)


@dp.message(CreateContactState.city, F.text)
async def add_city_message(message: Message, state: FSMContext):
    dct_state = await state.get_data()
    country_id = dct_state["country_id"]

    try:
        city_ru, city_en, city_uz, position = message.text.split(",")
    except ValueError:
        await message.answer(
            text=f"{admin_alert['wrong']}{admin_example['help_create_city']}"
        )
        return

    async with session_maker() as session:
        await orm_add_city(
            session, country_id, city_ru, city_en, city_uz, int(position)
        )

    await state.clear()
    await message.answer("Город добавлен  ✅")


@dp.callback_query(CountryCbUpDe.filter(F.action == ActionsUpDe.edit))
async def edit_country_cb(
    callback_query: CallbackQuery, callback_data: CountryCbUpDe, state: FSMContext
):
    await state.update_data(country_id=callback_data.id)

    async with session_maker() as session:
        country = await get_country_by_id(session, callback_data.id)

    text_info = (
        "<b>Изменение государства!</b>\nСкопируйте и вставьте заменить тот текст который ошибочный\n\n"
        + f"{country.name_ru},{country.name_en},{country.name_uz},{country.position}"
    )

    await callback_query.message.answer(text_info)
    await state.set_state(ChangeObject.country_change)


@dp.message(ChangeObject.country_change, F.text)
async def edit_country(message: Message, state: FSMContext):
    state_data = await state.get_data()
    country_id = state_data["country_id"]

    try:
        country_ru, country_en, country_uz, position = message.text.split(",")
    except ValueError:
        await message.answer(
            text=f"{admin_alert['wrong']}{admin_example['help_create_country']}"
        )
        return

    async with session_maker() as session:
        await orm_update_country(
            session, country_id, country_ru, country_en, country_uz, int(position)
        )

    await state.clear()
    await message.answer("Изменение выполнены  ✅")


@dp.callback_query(CountryCbUpDe.filter(F.action == ActionsUpDe.confirm_delete))
async def confirm_delete_country(
    callback_query: CallbackQuery, callback_data: CountryCbUpDe
):
    await callback_query.message.edit_text(
        text="<b>Вы точно хотите удалить государство?\nВсе связанные объекты будут удалены</b>",
        reply_markup=confirm_delete(
            [
                CountryCbUpDe(id=callback_data.id, action=ActionsUpDe.delete).pack(),
                CountryCbUpDe(
                    id=callback_data.id, action=ActionsUpDe.select_action
                ).pack(),
            ]
        ),
    )


@dp.callback_query(CountryCbUpDe.filter(F.action == ActionsUpDe.delete))
async def delete_country(callback_query: CallbackQuery, callback_data: CountryCbUpDe):
    async with session_maker() as session:
        country = await get_country_by_id(session, callback_data.id)
        await orm_delete_country(session, callback_data.id)

    await show_country_list(callback_query, 0)
    await callback_query.answer(f"Вы удалили {country.name_ru}")


@dp.callback_query(CountryPagination.filter())
async def back_country_list(
    callback_query: CallbackQuery, callback_data: CountryPagination
):
    await show_country_list(callback_query, callback_data.page)


# 1города
async def show_city_list(callback_query: CallbackQuery, country_id: int, page: int):
    async with session_maker() as session:
        cities = await get_all_cities(session, country_id)

    await callback_query.message.edit_text(
        text=(line + "\n" + "<b>Выберите город:\n</b>"),
        reply_markup=list_location_kb(
            cities,
            CityCbUpDe,
            lambda page: CityPagination(id=country_id, page=page),
            page,
            CountryPagination(page=0).pack(),
        ),
    )


@dp.callback_query(CityPagination.filter())
async def start_city_page(callback_query: CallbackQuery, callback_data: CityPagination):
    await show_city_list(callback_query, callback_data.id, callback_data.page)


@dp.callback_query(CityCbUpDe.filter(F.action == ActionsUpDe.select_action))
async def chouse_city_dop(callback_query: CallbackQuery, callback_data: CityCbUpDe):
    text_info = "<b>Выберите действие!</b>"
    city_id = callback_data.id

    async with session_maker() as session:
        city = await get_city_by_id(session, city_id)

    await callback_query.message.edit_text(
        text_info,
        reply_markup=choise_location_kb(
            name="улиц",
            add_name="улицу",
            callbacks_lst=[
                DistrictPagination(id=city_id, page=0).pack(),
                DistrictCbUpDe(id=city_id, action=ActionsUpDe.add).pack(),
                CityCbUpDe(id=city_id, action=ActionsUpDe.edit).pack(),
                CityCbUpDe(id=city_id, action=ActionsUpDe.confirm_delete).pack(),
                CityPagination(id=city.state_id, page=0).pack(),
            ],
        ),
    )


@dp.callback_query(DistrictCbUpDe.filter(F.action == ActionsUpDe.add))
async def add_district_cb(
    callback_query: CallbackQuery, callback_data: DistrictCbUpDe, state: FSMContext
):
    await state.update_data(city_id=callback_data.id)
    await callback_query.message.answer(
        text=f"Введите название улицы\n{admin_example['help_create_street']}",
    )
    await callback_query.answer()
    await state.set_state(CreateContactState.district_info)


@dp.message(CreateContactState.district_info, F.text)
async def add_district_name(message: Message, state: FSMContext):
    dct_state = await state.get_data()
    city_id = dct_state["city_id"]

    try:
        district_ru, district_en, district_uz = message.text.split(";")
    except ValueError:
        await message.answer(
            text=f"{admin_alert['wrong']}{admin_example['help_create_street']}"
        )
        return

    async with session_maker() as session:
        district_id = await orm_add_district_names(
            session, city_id, district_ru, district_en, district_uz
        )

    await state.update_data(district_id=district_id)

    await message.answer(
        f"Названия улицы добавлено!\nТеперь добавьте сначала <b>широту,долготу</b>\n{admin_example['help_create_ll']}"
    )
    await state.set_state(CreateContactState.location)


@dp.message(CreateContactState.location, F.text)
async def add_district_location(message: Message, state: FSMContext):
    state_data = await state.get_data()
    district_id = state_data["district_id"]

    try:
        latitude, longitude = map(float, message.text.split(","))
    except ValueError:
        await message.answer(
            text=f"{admin_alert['wrong']}{admin_example['help_create_city']}"
        )
        return

    async with session_maker() as session:
        await orm_update_district_location(session, district_id, latitude, longitude)

    await message.answer(
        text="Вы добабавили широту и долготу\nТеперь добавьте номер или номера через ','\n"
        + admin_example["help_create_number"]
    )
    await state.set_state(CreateContactState.contacts)


# номера
@dp.message(CreateContactState.contacts)
async def add_district_numbers(message: Message, state: FSMContext):
    cb_data = await state.get_data()
    district_id = cb_data["district_id"]

    numbers = message.text.split(",") if "," in message.text else [message.text]
    for number in numbers:
        async with session_maker() as session:
            await orm_add_number(session, district_id, number)

    await state.clear()
    await message.answer(
        text="Вы добабавили номера!",
    )


@dp.callback_query(CityCbUpDe.filter(F.action == ActionsUpDe.edit))
async def edit_city_cb(
    callback_query: CallbackQuery, callback_data: CityCbUpDe, state: FSMContext
):
    await state.update_data(city_id=callback_data.id)

    async with session_maker() as session:
        city = await get_city_by_id(session, callback_data.id)

    text_info = (
        "<b>Изменение города!</b>\nСкопируйте и вставьте заменить тот текст который ошибочный\n\n"
        + f"{city.name_ru},{city.name_en},{city.name_uz},{city.position}"
    )

    await callback_query.message.answer(text_info)
    await state.set_state(ChangeObject.city_change)


@dp.message(ChangeObject.city_change, F.text)
async def edit_city(message: Message, state: FSMContext):
    state_data = await state.get_data()
    city_id = state_data["city_id"]

    try:
        city_ru, city_en, city_uz, position = message.text.split(",")
    except ValueError:
        await message.answer(
            text=f"{admin_alert['wrong']}{admin_example['help_create_city']}"
        )
        return

    async with session_maker() as session:
        await orm_update_city(
            session, city_id, city_ru, city_en, city_uz, int(position)
        )

    await state.clear()
    await message.answer("Изменение выполнены  ✅")


@dp.callback_query(CityCbUpDe.filter(F.action == ActionsUpDe.confirm_delete))
async def confirm_delete_city(callback_query: CallbackQuery, callback_data: CityCbUpDe):
    await callback_query.message.edit_text(
        text="<b>Вы точно хотите удалить город?\nВсе связанные объекты будут удалены</b>",
        reply_markup=confirm_delete(
            [
                CityCbUpDe(id=callback_data.id, action=ActionsUpDe.delete).pack(),
                CityCbUpDe(
                    id=callback_data.id, action=ActionsUpDe.select_action
                ).pack(),
            ]
        ),
    )


@dp.callback_query(CityCbUpDe.filter(F.action == ActionsUpDe.delete))
async def delete_city(callback_query: CallbackQuery, callback_data: CityCbUpDe):
    async with session_maker() as session:
        city = await get_city_by_id(session, callback_data.id)
        await orm_delete_city(session, callback_data.id)

    await show_city_list(callback_query, city.state_id, 0)
    await callback_query.answer(f"Вы удалили {city.name_ru}")


@dp.callback_query(CityPagination.filter())
async def back_city_list(callback_query: CallbackQuery, callback_data: CityPagination):
    await show_city_list(callback_query, callback_data.id, callback_data.page)


# 1улицы
async def show_district_list(callback_query: CallbackQuery, city_id: int, page: int):
    async with session_maker() as session:
        city = await get_city_by_id(session, city_id)
        districts = await get_all_districts(session, city_id)

    await callback_query.message.edit_text(
        text=(line + "<b>Выберите улицу!</b>"),
        reply_markup=list_location_kb(
            districts,
            DistrictCbUpDe,
            DistrictPagination(id=city_id, page=page),
            page,
            CityPagination(id=city.state_id, page=0).pack(),
            1,
        ),
    )


@dp.callback_query(DistrictPagination.filter())
async def start_district_page(
    callback_query: CallbackQuery, callback_data: DistrictPagination
):
    await show_district_list(callback_query, callback_data.id, callback_data.page)


@dp.callback_query(DistrictCbUpDe.filter(F.action == ActionsUpDe.select_action))
async def chouse_district_dop(
    callback_query: CallbackQuery, callback_data: DistrictCbUpDe
):
    text_info = "<b>Выберите действие!</b>"
    district_id = callback_data.id

    async with session_maker() as session:
        district = await get_district_by_id(session, district_id)

    await callback_query.message.edit_text(
        text_info,
        reply_markup=choise_district_kb(
            callbacks_lst=[
                DistrictCbUpDe(id=district_id, action=ActionsUpDe.edit).pack(),
                DistrictCbUpDe(
                    id=district_id, action=ActionsUpDe.confirm_delete
                ).pack(),
                DistrictPagination(id=district.city_id, page=0).pack(),
            ],
        ),
    )


@dp.callback_query(DistrictCbUpDe.filter(F.action == ActionsUpDe.edit))
async def edit_district_cb(
    callback_query: CallbackQuery, callback_data: DistrictCbUpDe
):
    async with session_maker() as session:
        district = await get_district_by_id(session, callback_data.id)
        numbers = await get_numbers_by_district_id(session, district.id)

    text_info = (
        "<b>Изменение улицы!</b>\n"
        + f"<b>Названия</b> {district.name_ru};{district.name_en};{district.name_uz}\n"
        + f"<b>Локация</b> {district.latitude},{district.longitude}\n"
        + f"<b>Номера</b> {','.join(number.number for number in numbers)}"
    )

    await callback_query.message.edit_text(
        text_info, reply_markup=change_district(district.id)
    )


@dp.callback_query(DistrictChangeName.filter())
async def edit_district_name_cb(
    callback_query: CallbackQuery, callback_data: DistrictChangeName, state: FSMContext
):
    await state.update_data(district_id=callback_data.id)
    await state.set_state(ChangeObject.name_change_district)
    await callback_query.message.answer(text="<b>Изменение названия</b>")
    await callback_query.answer()


@dp.message(ChangeObject.name_change_district, F.text)
async def edit_distcrict_name(message: Message, state: FSMContext):
    state_data = await state.get_data()
    district_id = state_data["district_id"]

    try:
        district_ru, district_en, district_uz = message.text.split(";")
    except ValueError:
        await message.answer(
            text=f"{admin_alert['wrong']}{admin_example['help_create_street']}"
        )
        return

    async with session_maker() as session:
        await orm_update_district_names(
            session, district_id, district_ru, district_en, district_uz
        )

    await state.clear()
    await message.answer("Изменение выполнены  ✅")


@dp.callback_query(DistrictChangeLocation.filter())
async def edit_district_location_cb(
    callback_query: CallbackQuery,
    callback_data: DistrictChangeLocation,
    state: FSMContext,
):
    await state.update_data(district_id=callback_data.id)
    await state.set_state(ChangeObject.location_change_district)
    await callback_query.message.answer(text="<b>Изменение локации!</b>")
    await callback_query.answer()


@dp.message(ChangeObject.location_change_district, F.text)
async def edit_distcrict_location(message: Message, state: FSMContext):
    state_data = await state.get_data()
    district_id = state_data["district_id"]

    try:
        latitude, longitude = map(float, message.text.split(","))
    except ValueError:
        await message.answer(
            text=f"{admin_alert['wrong']}{admin_example['help_create_ll']}"
        )
        return

    async with session_maker() as session:
        await orm_update_district_location(session, district_id, latitude, longitude)

    await state.clear()
    await message.answer("Изменение выполнены  ✅")


@dp.callback_query(DistrictChangeNumbers.filter())
async def edit_district_numbers_cb(
    callback_query: CallbackQuery, callback_data: DistrictChangeName, state: FSMContext
):
    async with session_maker() as session:
        numbers = await get_numbers_by_district_id(session, callback_data.id)

    await callback_query.message.edit_text(
        text="<b>Выберите номер чтобы изменить его!</b>",
        reply_markup=numbers_kb(numbers, callback_data.id),
    )


@dp.callback_query(DistrictChangeNumber.filter())
async def edit_district_number_cb(
    callback_query: CallbackQuery, callback_data: DistrictChangeName, state: FSMContext
):
    await state.update_data(number_id=callback_data.id)
    await state.set_state(ChangeObject.number_change_district)
    await callback_query.message.answer("Введите новый номер чтобы изменить его!")


@dp.message(ChangeObject.number_change_district, F.text)
async def edit_distcrict_number(message: Message, state: FSMContext):
    state_data = await state.get_data()
    number_id = state_data["number_id"]

    async with session_maker() as session:
        await orm_update_number(session, number_id, message.text)

    await message.answer("Изменение выполнены  ✅")
    await state.clear()


@dp.callback_query(DistrictCbUpDe.filter(F.action == ActionsUpDe.confirm_delete))
async def confirm_delete_district(
    callback_query: CallbackQuery, callback_data: DistrictCbUpDe
):
    await callback_query.message.edit_text(
        text="<b>Вы точно хотите удалить удицу?\nВсе связанные объекты будут удалены</b>",
        reply_markup=confirm_delete(
            [
                DistrictCbUpDe(id=callback_data.id, action=ActionsUpDe.delete).pack(),
                DistrictCbUpDe(
                    id=callback_data.id, action=ActionsUpDe.select_action
                ).pack(),
            ]
        ),
    )


@dp.callback_query(DistrictCbUpDe.filter(F.action == ActionsUpDe.delete))
async def delete_district(callback_query: CallbackQuery, callback_data: DistrictCbUpDe):
    async with session_maker() as session:
        district = await get_district_by_id(session, callback_data.id)
        await orm_delete_district(session, callback_data.id)

    await show_district_list(callback_query, district.city_id, 0)
    await callback_query.answer(f"Вы удалили {district.name_ru}")


@dp.callback_query(DistrictPagination.filter())
async def back_district_list(
    callback_query: CallbackQuery, callback_data: DistrictPagination
):
    await show_district_list(callback_query, callback_data.id, callback_data.page)


# конец админки
@dp.message(F.text == ChoicesKeyboardAddContact.back)
@dp.message(or_f(CommandStart(), F.text == __("🌐 Сменить язык")))
async def start_command(
    message: Message,
    i18n_middleware: FSMI18nMiddleware,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not message.chat.id == group_id:
        user_id = message.from_user.id
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        await state.set_state(Menu.choose_language)
        await message.answer(
            _(
                "Привет! Рады видеть вас в нашем боте для отслеживания посылок 📦. Получайте обновления о "
                "статусе ваших отправлений и другие полезные новости 🗞\n\nВыберите, пожалуйста, язык!"
            ),
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[
                    [types.KeyboardButton(text="🇷🇺Русский")],
                    [types.KeyboardButton(text="🇺🇿O'zbekcha")],
                    [types.KeyboardButton(text="🇬🇧English")],
                ],
                resize_keyboard=True,
            ),
        )
    data = {
        "name": message.from_user.first_name,
        "username": message.from_user.username,
        "user_id": message.from_user.id,
        "message_text": message.text,
        "message_id": message.message_id,
        "status": True,
    }
    f = open("users_id.txt", "a", encoding=("utf-8"))
    f.write(f"{data['user_id']}\n")
    f.close()
    a = open("users_id.txt", "r", encoding=("utf-8"))
    print(a.read())
    # response = requests.post('http://178.20.45.210:8011/api/v1/message/', data=data)


@dp.message(Menu.choose_language)
async def main_menu(
    message: types.Message,
    i18n_middleware: FSMI18nMiddleware,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    languages = {"🇷🇺Русский": "ru", "🇺🇿O'zbekcha": "uz", "🇬🇧English": "en"}
    chosen_language = languages.get(message.text)

    # новый код
    request = Request(session)
    await request.add_user(message.from_user.id, languages[message.text])
    # Конец

    if not chosen_language:
        await message.answer(_("Пожалуйста, выберите корректное значение"))
    await i18n_middleware.set_locale(state=state, locale=chosen_language)
    await state.set_state(Menu.main_menu)
    if message.from_user.id in user_list:
        await message.answer(
            _("Главное меню"),
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[
                    [
                        types.KeyboardButton(text=_("🔍 Отследить посылку")),
                        types.KeyboardButton(text=_("👤 Контакты")),
                    ],
                    [
                        types.KeyboardButton(text=_("📃 Тарифы")),
                        types.KeyboardButton(text=_("🌐 Сменить язык")),
                    ],
                    [
                        types.KeyboardButton(text=_("Информация")),
                        types.KeyboardButton(text=_("Статистика бота")),
                    ],
                    [types.KeyboardButton(text=_("Поддержка"))],
                    [types.KeyboardButton(text=_("Соц сети"))],
                    [KeyboardButton(  # новая кнопка
                        text=_("Наш сайт"),
                        web_app=WebAppInfo(url="https://atlasexpress.uz/")
                    )]
                ],
                resize_keyboard=True,
            ),
        )
    else:
        await message.answer(
            _("Главное меню"),
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[
                    [
                        types.KeyboardButton(text=_("🔍 Отследить посылку")),
                        types.KeyboardButton(text=_("👤 Контакты")),
                    ],
                    [
                        types.KeyboardButton(text=_("📃 Тарифы")),
                        types.KeyboardButton(text=_("🌐 Сменить язык")),
                    ],
                    [types.KeyboardButton(text=_("Информация"))],
                    [types.KeyboardButton(text=_("Поддержка"))],
                    [types.KeyboardButton(text=_("Соц сети"))],
                    [KeyboardButton(  # новая кнопка
                        text=_("Наш сайт"),
                        web_app=WebAppInfo(url="https://atlasexpress.uz/")
                    )]
                ],
                resize_keyboard=True,
            ),
        )
    data = {
        "name": message.from_user.first_name,
        "username": message.from_user.username,
        "user_id": message.from_user.id,
        "message_text": message.text,
        "message_id": message.message_id,
        "status": True,
    }
    # response = requests.post('http://178.20.45.210:8011/api/v1/message/', data=data)
    # print(message)


@dp.message(Menu.main_menu, F.text == __("🔍 Отследить посылку"))
async def check_id(message: types.Message, state: FSMContext) -> None:
    await message.answer(
        _("Пожалуйста, введите номер коробки"),
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text=_("❌ Отмена"))]], resize_keyboard=True
        ),
    )
    await state.set_state(Menu.check_id)
    data = {
        "name": message.from_user.first_name,
        "username": message.from_user.username,
        "user_id": message.from_user.id,
        "message_text": message.text,
        "message_id": message.message_id,
        "status": True,
    }
    # response = requests.post('http://178.20.45.210:8011/api/v1/message/', data=data)


@dp.message(F.text == __("❌ Отмена"))
async def check_id(message: types.Message, state: FSMContext) -> None:
    await state.set_state(Menu.main_menu)
    if message.from_user.id in user_list:
        await message.answer(
            _("Главное меню"),
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[
                    [
                        types.KeyboardButton(text=_("🔍 Отследить посылку")),
                        types.KeyboardButton(text=_("👤 Контакты")),
                    ],
                    [
                        types.KeyboardButton(text=_("📃 Тарифы")),
                        types.KeyboardButton(text=_("🌐 Сменить язык")),
                    ],
                    [
                        types.KeyboardButton(text=_("Информация")),
                        types.KeyboardButton(text=_("Статистика бота")),
                    ],
                    [types.KeyboardButton(text=_("Поддержка"))],
                    [types.KeyboardButton(text=_("Соц сети"))],
                    [KeyboardButton(  # новая кнопка
                        text=_("Наш сайт"),
                        web_app=WebAppInfo(url="https://atlasexpress.uz/")
                    )]
                ],
                resize_keyboard=True,
            ),
        )
    else:
        await message.answer(
            _("Главное меню"),
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[
                    [
                        types.KeyboardButton(text=_("🔍 Отследить посылку")),
                        types.KeyboardButton(text=_("👤 Контакты")),
                    ],
                    [
                        types.KeyboardButton(text=_("📃 Тарифы")),
                        types.KeyboardButton(text=_("🌐 Сменить язык")),
                    ],
                    [types.KeyboardButton(text=_("Информация"))],
                    [types.KeyboardButton(text=_("Поддержка"))],
                    [types.KeyboardButton(text=_("Соц сети"))],
                    [KeyboardButton(  # новая кнопка
                        text=_("Наш сайт"),
                        web_app=WebAppInfo(url="https://atlasexpress.uz/")
                    )]
                ],
                resize_keyboard=True,
            ),
        )


@dp.message(Menu.check_id)
async def id_typed(
    message: types.Message,
):
    id_ = message.text
    statuses = {
        "location": _("location"),
        "in_driver": _("in_driver"),
        "in_warehouse": _("in_warehouse"),
        "packed": _("packed"),
        "shipped": _("shipped"),
        "departed": _("departed"),
        "in_transit": _("in_transit"),
        "arrived": _("arrived"),
        "in_customs": _("in_customs"),
        "arrive_warehouse": _("arrive_warehouse"),
        "out_location": _("out_location"),
        "accept_location": _("accept_location"),
        "out_driver": _("out_driver"),
        "delivered": _("delivered"),
        "not_delivered": _("not_delivered"),
        "refund": _("refund"),
    }
    rates_dict = {
        "USA-UZB EXPRESS": _("USA-UZB EXPRESS"),
        "UZB-USA Express": _("UZB-USA Express"),
        "USA-UZB STANDARD": _("USA-UZB STANDARD"),
        "UZB-USA Standard": _("UZB-USA Standard"),
        "USA-UZB EXPRESS COMMERCIAL": _("USA-UZB EXPRESS COMMERCIAL"),
        "USA-UZB GROUND": _("USA-UZB GROUND"),
        "UZB-USA GroundUZ": _("UZB-USA GroundUZ"),
    }
    five_express_day = ["location", "in_driver", "in_warehouse", "packed"]
    four_express_day = ["shipped", "departed", "in_transit"]
    three_express_day = [
        "arrived",
        "in_customs",
        "arrive_warehouse",
        "out_location",
        "accept_location",
    ]
    rates_type = ["USA-UZB EXPRESS", "UZB-USA Express", "USA-UZB EXPRESS COMMERCIAL"]
    one_day_status = ["out_location", "accept_location", "out_driver"]
    rate_ground = ["USA-UZB GROUND", "USA-UZB STANDARD"]
    rate_ground_uz = ["UZB-USA Standard", "UZB-USA GroundUZ"]
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"http://app.fgf.ai/trackings/box?country=&identityNumber=&number={id_}",
            headers={"Accept": "application/json"},
        ) as response:
            response_json = await response.json()

            if response_json == "Not found box":
                return await message.answer(_("По вашему запросу ничего не найдено"))

            status = statuses.get(response_json["status"])
            rates = rates_dict.get(response_json["shipmentType"]["name"])
            if not status:
                status = response_json["status"]
            if (
                response_json["status"] == "location"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 18 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "in_driver"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 17 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "in_warehouse"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 16 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "packed"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 15 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "shipped"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 14 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "departed"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    # + _("Доставка будет осуществлена в течение 13 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "in_transit"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 11 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "arrived"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 9 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "in_customs"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 7 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "arrive_warehouse"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 5 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "out_location"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 4 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "accept_location"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + str(response_json["currentLocation"]["address"])
                    + "\n"
                    + _("Тел номер: ")
                    + str(response_json["currentLocation"]["phone"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 3 дней.")
                )
            elif (
                response_json["status"] == "out_driver"
                and response_json["shipmentType"]["name"] in rate_ground
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение дня.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] in five_express_day
                and response_json["shipmentType"]["name"] in rates_type
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 5 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] in four_express_day
                and response_json["shipmentType"]["name"] in rates_type
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 4 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "accept_location"
                and response_json["shipmentType"]["name"] in rates_type
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + str(response_json["currentLocation"]["address"])
                    + "\n"
                    + _("Тел номер: ")
                    + str(response_json["currentLocation"]["phone"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 3 дней.")
                )
            elif (
                response_json["status"] in three_express_day
                and response_json["shipmentType"]["name"] in rates_type
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 3 дней.")
                )
            elif (
                response_json["status"] == "out_driver"
                and response_json["shipmentType"]["name"] in rates_type
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение дня.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "location"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 14 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "in_driver"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 13 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "in_warehouse"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 12 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "packed"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 10 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "shipped"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 8 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "departed"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 5 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "in_transit"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 4 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "arrived"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 3 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "is_customs"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение 2 дней.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] == "arrive_warehouse"
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Готовы к получению и отправке в другие штаты.")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif (
                response_json["status"] in one_day_status
                and response_json["shipmentType"]["name"] in rate_ground_uz
            ):
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставка будет осуществлена в течение дня")
                    # + "\n"
                    # + _("Приблизительная дата прибытия")
                    # + str(response_json['estimatedArrival'])
                )
            elif response_json["status"] == "delivered":
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Ваш тариф: ")
                    + str(rates)
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                    + _("Текущее местоположение: ")
                    + str(response_json["currentLocation"]["name"])
                    + "\n"
                    + _("Доставлено: ")
                    + str(response_json["updatedAt"])
                )
            else:
                await message.answer(
                    str(_("Invoice Number: "))
                    + str(response_json["id"])
                    + "\n"
                    + _("Статус")
                    + ": "
                    + str(status)
                    + "\n"
                )


# Новые контакты
def get_localized_name(obj, lang: str) -> str:
    """Получает локализованное название объекта на основе выбранного языка."""
    return getattr(obj, f"name_{lang}", obj.name_ru)  # Русский как запасной вариант


def get_localized_delivery_text(obj, lang: str) -> str:
    """Получает локализованный текст доставки на основе выбранного языка."""
    return getattr(obj, f"delivery_text_{lang}", obj.delivery_text_ru)


class CountryCallback(CallbackData, prefix="country"):
    id: int


class CityCallback(CallbackData, prefix="city"):
    id: int


class DistrictCallback(CallbackData, prefix="district"):
    id: int


class CountryPageCallback(CallbackData, prefix="country_page"):
    page: int


class CityPageCallback(CallbackData, prefix="city_page"):
    country_id: int
    page: int


class TariffFromCountryCallback(CallbackData, prefix="tariff_from"):
    id: int


class TariffFromPageCallback(CallbackData, prefix="tariff_from_page"):
    page: int


class TariffToCountryCallback(CallbackData, prefix="tariff_to"):
    from_country_id: int
    to_country_id: int


class TariffToPageCallback(CallbackData, prefix="tariff_to_page"):
    from_country_id: int
    page: int


class TariffDetailCallback(CallbackData, prefix="tariff_detail"):
    id: int


class TariffAdminActionCallback(CallbackData, prefix="tariff_admin_action"):
    action: str


class TariffAdminFromCountryCallback(CallbackData, prefix="tariff_admin_from"):
    id: int


class TariffAdminFromPageCallback(CallbackData, prefix="tariff_admin_from_page"):
    page: int


class TariffAdminToCountryCallback(CallbackData, prefix="tariff_admin_to"):
    id: int


class TariffAdminToPageCallback(CallbackData, prefix="tariff_admin_to_page"):
    page: int


class TariffAdminCategoryCallback(CallbackData, prefix="tariff_admin_category"):
    id: int


class TariffAdminCategoryPageCallback(CallbackData, prefix="tariff_admin_category_page"):
    page: int


async def get_user_language(user_id: int) -> str:
    async with session_maker() as session:
        user = await get_language(session, user_id)
    return user.language


@dp.message(F.text == ChoicesKeyboardAddContact.user_contacts)
@dp.message(F.text == __("👤 Контакты"))
async def new_contacts(message: Message):
    data = {
        "name": message.from_user.first_name,
        "username": message.from_user.username,
        "user_id": message.from_user.id,
        "message_text": message.text,
        "message_id": message.message_id,
        "status": True,
    }
    # response = requests.post('http://178.20.45.210:8011/api/v1/message/', data=data)
    await show_countries_page(message, page=0)


async def show_countries_page(message_or_callback, page: int):
    user_id = message_or_callback.from_user.id
    if isinstance(message_or_callback, Message):
        async with session_maker() as session:
            await orm_add_user(session, user_id)

    async with session_maker() as session:
        lang = await get_user_language(user_id)
        countryes = await get_all_countryes(session)

    paginator = Paginator(countryes, page)
    countryes_slice = paginator.get_current_page_items()

    builder = InlineKeyboardBuilder()
    for country in countryes_slice:
        builder.button(
            text=get_localized_name(country, lang),
            callback_data=CountryCallback(id=country.id).pack(),
        )

    pagination_buttons = paginator.get_pagination_buttons(CountryPageCallback)
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.adjust(2)

    text = line + "\n" + _("<b>🗾 Выберите государство:</b>")
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(
            text, reply_markup=builder.as_markup(resize_keyboard=True)
        )
    else:
        await message_or_callback.message.edit_text(
            text, reply_markup=builder.as_markup(resize_keyboard=True)
        )


@dp.callback_query(CountryCallback.filter())
async def choose_city(callback: CallbackQuery, callback_data: CountryCallback):
    await show_cities_page(callback, callback_data.id, page=0)


@dp.callback_query(CountryPageCallback.filter())
async def paginate_countries(
    callback: CallbackQuery, callback_data: CountryPageCallback
):
    await show_countries_page(callback, page=callback_data.page)


@dp.callback_query(CityPageCallback.filter())
async def paginate_cities(callback: CallbackQuery, callback_data: CityPageCallback):
    await show_cities_page(callback, callback_data.country_id, page=callback_data.page)


async def show_cities_page(callback, country_id: int, page: int):
    user_id = callback.from_user.id
    lang = await get_user_language(user_id)

    async with session_maker() as session:
        cities = await get_all_cities(session, country_id)

    paginator = Paginator(cities, page)
    cities_slice = paginator.get_current_page_items()

    builder = InlineKeyboardBuilder()
    for city in cities_slice:
        builder.button(
            text=get_localized_name(city, lang),
            callback_data=CityCallback(id=city.id).pack(),
        )

    pagination_buttons = paginator.get_pagination_buttons(
        lambda page: CityPageCallback(country_id=country_id, page=page)
    )
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.adjust(2)

    builder.row(
        InlineKeyboardButton(
            text=_("Назад"), callback_data=CountryPageCallback(page=0).pack()
        )
    )
    await callback.message.edit_text(
        text=(line + "\n" + _("<b>🌇 Выберите город:</b>")),
        reply_markup=builder.as_markup(resize_keyboard=True),
    )


@dp.callback_query(CityCallback.filter())
async def show_streets_info(
    callback: CallbackQuery, callback_data: CityCallback, session: AsyncSession
) -> None:
    city_id = callback_data.id
    user_id = callback.from_user.id

    lang = await get_user_language(user_id)

    async with session_maker() as session:
        districts = await get_all_districts(session, city_id)

    if not districts:
        await callback.answer(_("Ошибка: в этом городе нет данных о улицах."))
        return

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.delete()

    for district in districts:
        async with session_maker() as session:
            numbers = await get_numbers_by_district_id(session, district.id)

        numbers_text = (
            "\n".join(f"📞 {num.number}" for num in numbers)
            if numbers
            else _("Нет номеров")
        )

        location: str = ""
        local_text = _("Местоположение")
        if district.latitude != 0.0 and district.longitude != 0.0:
            location: str = f"🌎 <a href='https://maps.google.com/?q={district.latitude},{district.longitude}'>{local_text}</a>\n"

        text = (
            f"📍 <b>{get_localized_name(district, lang)}</b>\n"
            + location
            + f"{numbers_text}"
        )

        await callback.message.answer(text, disable_web_page_preview=True)


@dp.message(F.text == "/broadcast")
async def start_broadcast(message: Message, state: FSMContext):
    admin_id = 960217500  # Замените на ваш ID
    if message.from_user.id not in user_list:
        await message.answer("У вас нет прав на выполнение этой команды.")
        return

    # Устанавливаем состояние
    await state.set_state(BroadcastState.waiting_for_message)
    cancel_button = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "Отправьте сообщение или фото для рассылки, либо нажмите 'Отмена'.",
        reply_markup=cancel_button,
    )


# Обработка сообщений в состоянии ожидания
@dp.message(BroadcastState.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext):
    admin_id = 960217500  # Замените на ваш ID
    if message.from_user.id not in user_list:
        await message.answer("У вас нет прав на выполнение этой команды.")
        return

    if message.text and message.text.lower() == "❌ Отмена":
        await state.clear()
        await message.answer(
            "Рассылка отменена.", reply_markup=types.ReplyKeyboardRemove()
        )
        return

    # Получаем список пользователей
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    # Рассылка
    success = 0
    failed = 0
    for user_id in users:
        try:
            if message.photo:  # Если фото
                await bot.send_photo(
                    chat_id=user_id[0],
                    photo=message.photo[-1].file_id,
                    caption=message.caption,
                )
            elif message.text:  # Если текст
                await bot.send_message(chat_id=user_id[0], text=message.text)
            success += 1
        except TelegramBadRequest:
            failed += 1

    # После завершения рассылки отправляем сообщение и оставляем кнопку "Отмена"
    cancel_button = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        f"Рассылка завершена.\nУспешно: {success}\nНеудачно: {failed}.",
        reply_markup=cancel_button,  # Оставляем кнопку "Отмена"
    )

    # Ожидаем следующую рассылку или команду "Отмена"
    await state.set_state(BroadcastState.waiting_for_message)


@dp.message(F.text == "/users")
async def show_users(message: Message):
    admin_id = 960217500  # Замените на ваш ID
    if message.from_user.id != admin_id:
        await message.answer("У вас нет прав на выполнение этой команды.")
        return

    # Получаем всех пользователей
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    if not users:
        await message.answer("Список пользователей пуст.")
    else:
        # Подсчитываем количество пользователей
        user_count = len(users)
        user_list = "\n".join([str(user[0]) for user in users])
        await message.answer(
            f"Зарегистрированные пользователи ({user_count}):\n{user_list}"
        )


@dp.message(F.text == __("📃 Тарифы"))
async def rates(message: types.Message, state: FSMContext):
    if is_admin_user(message.from_user.id):
        kb = InlineKeyboardBuilder()
        kb.button(
            text="⚙️ Управление тарифами",
            callback_data=TariffAdminActionCallback(action="menu").pack(),
        )
        await message.answer("Админ: управление тарифами", reply_markup=kb.as_markup())
    await show_tariff_from_countries_reply(message, state)
    data = {
        "name": message.from_user.first_name,
        "username": message.from_user.username,
        "user_id": message.from_user.id,
        "message_text": message.text,
        "message_id": message.message_id,
        "status": True,
    }
    # response = requests.post('http://178.20.45.210:8011/api/v1/message/', data=data)


async def show_tariff_from_countries_reply(message: Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_user_language(user_id)

    async with session_maker() as session:
        countries = await get_tariff_from_countries(session)

    if not countries:
        await message.answer(_("Тарифы пока недоступны."))
        return

    from_map = {get_localized_name(country, lang): country.id for country in countries}
    await message.answer(
        _("sel_traffic"),
        reply_markup=build_tariff_reply_keyboard(list(from_map.keys())),
    )
    await state.set_state(TariffUserState.select_from_country)
    await state.update_data(tariff_from_map=from_map)


def build_tariff_reply_keyboard(items: list[str]) -> ReplyKeyboardMarkup:
    keyboard = []
    row = []
    for item in items:
        row.append(types.KeyboardButton(text=item))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([types.KeyboardButton(text=_("❌ Отмена"))])
    return types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


@dp.message(TariffUserState.select_from_country, F.text)
async def user_select_tariff_from_country(message: Message, state: FSMContext):
    if message.text == _("❌ Отмена") or message.text == "❌ Отмена":
        await state.clear()
        return

    data = await state.get_data()
    from_map = data.get("tariff_from_map", {})
    from_country_id = from_map.get(message.text)
    if not from_country_id:
        await message.answer(_("Пожалуйста, выберите корректное значение"))
        return

    await state.update_data(from_country_id=from_country_id)
    await show_tariff_to_countries_reply(message, state, from_country_id)


async def show_tariff_to_countries_reply(
    message: Message, state: FSMContext, from_country_id: int
):
    user_id = message.from_user.id
    lang = await get_user_language(user_id)

    async with session_maker() as session:
        countries = await get_tariff_to_countries(session, from_country_id)

    if not countries:
        await message.answer(_("Тарифы пока недоступны."))
        await state.clear()
        return

    to_map = {get_localized_name(country, lang): country.id for country in countries}
    await message.answer(
        _("sel_rec_coun"),
        reply_markup=build_tariff_reply_keyboard(list(to_map.keys())),
    )
    await state.set_state(TariffUserState.select_to_country)
    await state.update_data(tariff_to_map=to_map)


@dp.message(TariffUserState.select_to_country, F.text)
async def user_select_tariff_to_country(message: Message, state: FSMContext):
    if message.text == _("❌ Отмена") or message.text == "❌ Отмена":
        await state.clear()
        return

    data = await state.get_data()
    to_map = data.get("tariff_to_map", {})
    to_country_id = to_map.get(message.text)
    if not to_country_id:
        await message.answer(_("Пожалуйста, выберите корректное значение"))
        return

    from_country_id = data.get("from_country_id")
    if not from_country_id:
        await message.answer(_("Пожалуйста, выберите корректное значение"))
        return

    user_id = message.from_user.id
    lang = await get_user_language(user_id)

    async with session_maker() as session:
        tariffs = await get_tariffs_for_route(session, from_country_id, to_country_id)

    if not tariffs:
        await message.answer(_("Тарифы для выбранного направления пока отсутствуют."))
        await state.clear()
        return

    await state.clear()

    for tariff in tariffs:
        title = (
            f"{get_localized_name(tariff.from_country, lang)} → "
            f"{get_localized_name(tariff.to_country, lang)}"
        )
        category = get_localized_name(tariff.category, lang)
        price = tariff.price
        delivery_text = get_localized_delivery_text(tariff, lang)

        text = f"{title}\n{category}\n{price}\n{delivery_text}\n"
        kb = InlineKeyboardBuilder()
        kb.button(
            text=_("Подробнее"),
            callback_data=TariffDetailCallback(id=tariff.id).pack(),
        )
        await message.answer(text, reply_markup=kb.as_markup())


async def show_tariff_from_countries(message_or_callback, page: int):
    user_id = message_or_callback.from_user.id
    lang = await get_user_language(user_id)

    async with session_maker() as session:
        countries = await get_tariff_from_countries(session)

    if not countries:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer(_("Тарифы пока недоступны."))
        else:
            await message_or_callback.message.edit_text(_("Тарифы пока недоступны."))
        return

    paginator = Paginator(countries, page)
    countries_slice = paginator.get_current_page_items()

    builder = InlineKeyboardBuilder()
    for country in countries_slice:
        builder.button(
            text=get_localized_name(country, lang),
            callback_data=TariffFromCountryCallback(id=country.id).pack(),
        )

    pagination_buttons = paginator.get_pagination_buttons(TariffFromPageCallback)
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.adjust(2)

    text = line + "\n" + _("sel_traffic")
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(
            text, reply_markup=builder.as_markup(resize_keyboard=True)
        )
    else:
        await message_or_callback.message.edit_text(
            text, reply_markup=builder.as_markup(resize_keyboard=True)
        )


@dp.callback_query(TariffFromCountryCallback.filter())
async def show_tariff_to_countries(
    callback: CallbackQuery, callback_data: TariffFromCountryCallback
):
    await show_tariff_to_countries_page(callback, callback_data.id, page=0)


@dp.callback_query(TariffFromPageCallback.filter())
async def paginate_tariff_from_countries(
    callback: CallbackQuery, callback_data: TariffFromPageCallback
):
    await show_tariff_from_countries(callback, page=callback_data.page)


async def show_tariff_to_countries_page(
    callback: CallbackQuery, from_country_id: int, page: int
):
    user_id = callback.from_user.id
    lang = await get_user_language(user_id)

    async with session_maker() as session:
        countries = await get_tariff_to_countries(session, from_country_id)

    if not countries:
        await callback.message.edit_text(_("Тарифы пока недоступны."))
        return

    paginator = Paginator(countries, page)
    countries_slice = paginator.get_current_page_items()

    builder = InlineKeyboardBuilder()
    for country in countries_slice:
        builder.button(
            text=get_localized_name(country, lang),
            callback_data=TariffToCountryCallback(
                from_country_id=from_country_id, to_country_id=country.id
            ).pack(),
        )

    pagination_buttons = paginator.get_pagination_buttons(
        lambda page: TariffToPageCallback(from_country_id=from_country_id, page=page)
    )
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text=_("Назад"), callback_data=TariffFromPageCallback(page=0).pack()
        )
    )

    await callback.message.edit_text(
        text=(line + "\n" + _("sel_rec_coun")),
        reply_markup=builder.as_markup(resize_keyboard=True),
    )


@dp.callback_query(TariffToPageCallback.filter())
async def paginate_tariff_to_countries(
    callback: CallbackQuery, callback_data: TariffToPageCallback
):
    await show_tariff_to_countries_page(
        callback, callback_data.from_country_id, callback_data.page
    )


@dp.callback_query(TariffToCountryCallback.filter())
async def show_tariffs_for_route(
    callback: CallbackQuery, callback_data: TariffToCountryCallback
):
    user_id = callback.from_user.id
    lang = await get_user_language(user_id)

    async with session_maker() as session:
        tariffs = await get_tariffs_for_route(
            session, callback_data.from_country_id, callback_data.to_country_id
        )

    if not tariffs:
        await callback.message.edit_text(_("Тарифы для выбранного направления пока отсутствуют."))
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.delete()

    for tariff in tariffs:
        title = (
            f"{get_localized_name(tariff.from_country, lang)} → "
            f"{get_localized_name(tariff.to_country, lang)}"
        )
        category = get_localized_name(tariff.category, lang)
        price = tariff.price
        delivery_text = get_localized_delivery_text(tariff, lang)

        text = f"{title}\n{category}\n{price}\n{delivery_text}\n"
        kb = InlineKeyboardBuilder()
        kb.button(text=_("Подробнее"), callback_data=TariffDetailCallback(id=tariff.id).pack())
        await callback.message.answer(text, reply_markup=kb.as_markup())

    await callback.answer()


@dp.callback_query(TariffDetailCallback.filter())
async def show_tariff_detail(
    callback: CallbackQuery, callback_data: TariffDetailCallback
):
    user_id = callback.from_user.id
    lang = await get_user_language(user_id)

    async with session_maker() as session:
        tariff = await get_tariff_by_id(session, callback_data.id)

    if tariff:
        delivery_text = get_localized_delivery_text(tariff, lang)
        await callback.message.answer(delivery_text)
    else:
        await callback.message.answer(_("Тарифы для выбранного направления пока отсутствуют."))
    await callback.answer()


def build_admin_tariff_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="➕ Добавить страну отправителя/получателя",
        callback_data=TariffAdminActionCallback(action="add_country").pack(),
    )
    kb.button(
        text="➕ Добавить категорию тарифа",
        callback_data=TariffAdminActionCallback(action="add_category").pack(),
    )
    kb.button(
        text="➕ Добавить тариф",
        callback_data=TariffAdminActionCallback(action="add_tariff").pack(),
    )
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(TariffAdminActionCallback.filter())
async def tariff_admin_actions(
    callback: CallbackQuery, callback_data: TariffAdminActionCallback, state: FSMContext
):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    if callback_data.action == "menu":
        await state.clear()
        await callback.message.answer("Управление тарифами:", reply_markup=build_admin_tariff_menu())
        await callback.answer()
        return

    if callback_data.action == "add_country":
        await state.clear()
        await callback.message.answer(
            "Введите страну в формате:\nCODE,Название RU,Name EN,Name UZ\n"
            "Пример: USA,🇺🇸 США,🇺🇸 USA,🇺🇸 AQSH"
        )
        await state.set_state(TariffAdminState.country)
        await callback.answer()
        return

    if callback_data.action == "add_category":
        await state.clear()
        await callback.message.answer(
            "Введите категорию в формате:\nCODE,Название RU,Name EN,Name UZ\n"
            "Пример: standard,Стандарт,Standard,Standart"
        )
        await state.set_state(TariffAdminState.category)
        await callback.answer()
        return

    if callback_data.action == "add_tariff":
        await state.clear()
        await state.set_state(TariffAdminState.select_from_country)
        await show_admin_tariff_from_countries(callback, page=0)
        await callback.answer()
        return

    await callback.answer()


@dp.message(TariffAdminState.country, F.text)
async def tariff_admin_add_country(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await message.answer("Нет доступа")
        return
    try:
        code, name_ru, name_en, name_uz = [part.strip() for part in message.text.split(",")]
    except ValueError:
        await message.answer("Неверный формат. Используйте: CODE,Название RU,Name EN,Name UZ")
        return

    async with session_maker() as session:
        await orm_add_tariff_country(session, code, name_ru, name_en, name_uz)

    await state.clear()
    await message.answer("Страна добавлена ✅", reply_markup=build_admin_tariff_menu())


@dp.message(TariffAdminState.category, F.text)
async def tariff_admin_add_category(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await message.answer("Нет доступа")
        return
    try:
        code, name_ru, name_en, name_uz = [part.strip() for part in message.text.split(",")]
    except ValueError:
        await message.answer("Неверный формат. Используйте: CODE,Название RU,Name EN,Name UZ")
        return

    async with session_maker() as session:
        await orm_add_tariff_category(session, code, name_ru, name_en, name_uz)

    await state.clear()
    await message.answer("Категория добавлена ✅", reply_markup=build_admin_tariff_menu())


async def show_admin_tariff_from_countries(message_or_callback, page: int):
    async with session_maker() as session:
        countries = await get_all_tariff_countries(session)

    if not countries:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer("Нет стран для выбора.")
        else:
            await message_or_callback.message.edit_text("Нет стран для выбора.")
        return

    paginator = Paginator(countries, page)
    countries_slice = paginator.get_current_page_items()

    builder = InlineKeyboardBuilder()
    for country in countries_slice:
        builder.button(
            text=country.name_ru,
            callback_data=TariffAdminFromCountryCallback(id=country.id).pack(),
        )

    pagination_buttons = paginator.get_pagination_buttons(TariffAdminFromPageCallback)
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.adjust(2)

    text = "Выберите страну отправителя:"
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text, reply_markup=builder.as_markup())
    else:
        await message_or_callback.message.edit_text(text, reply_markup=builder.as_markup())


async def show_admin_tariff_to_countries(message_or_callback, page: int):
    async with session_maker() as session:
        countries = await get_all_tariff_countries(session)

    if not countries:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer("Нет стран для выбора.")
        else:
            await message_or_callback.message.edit_text("Нет стран для выбора.")
        return

    paginator = Paginator(countries, page)
    countries_slice = paginator.get_current_page_items()

    builder = InlineKeyboardBuilder()
    for country in countries_slice:
        builder.button(
            text=country.name_ru,
            callback_data=TariffAdminToCountryCallback(id=country.id).pack(),
        )

    pagination_buttons = paginator.get_pagination_buttons(TariffAdminToPageCallback)
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.adjust(2)

    text = "Выберите страну получателя:"
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text, reply_markup=builder.as_markup())
    else:
        await message_or_callback.message.edit_text(text, reply_markup=builder.as_markup())


async def show_admin_tariff_categories(message_or_callback, page: int):
    async with session_maker() as session:
        categories = await get_all_tariff_categories(session)

    if not categories:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer("Нет категорий для выбора.")
        else:
            await message_or_callback.message.edit_text("Нет категорий для выбора.")
        return

    paginator = Paginator(categories, page)
    categories_slice = paginator.get_current_page_items()

    builder = InlineKeyboardBuilder()
    for category in categories_slice:
        builder.button(
            text=category.name_ru,
            callback_data=TariffAdminCategoryCallback(id=category.id).pack(),
        )

    pagination_buttons = paginator.get_pagination_buttons(TariffAdminCategoryPageCallback)
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.adjust(2)

    text = "Выберите категорию тарифа:"
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text, reply_markup=builder.as_markup())
    else:
        await message_or_callback.message.edit_text(text, reply_markup=builder.as_markup())


@dp.callback_query(TariffAdminFromPageCallback.filter())
async def paginate_admin_tariff_from(
    callback: CallbackQuery, callback_data: TariffAdminFromPageCallback
):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await show_admin_tariff_from_countries(callback, page=callback_data.page)


@dp.callback_query(TariffAdminToPageCallback.filter())
async def paginate_admin_tariff_to(
    callback: CallbackQuery, callback_data: TariffAdminToPageCallback
):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await show_admin_tariff_to_countries(callback, page=callback_data.page)


@dp.callback_query(TariffAdminCategoryPageCallback.filter())
async def paginate_admin_tariff_category(
    callback: CallbackQuery, callback_data: TariffAdminCategoryPageCallback
):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await show_admin_tariff_categories(callback, page=callback_data.page)


@dp.callback_query(TariffAdminFromCountryCallback.filter())
async def admin_select_from_country(
    callback: CallbackQuery, callback_data: TariffAdminFromCountryCallback, state: FSMContext
):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.update_data(from_country_id=callback_data.id)
    await state.set_state(TariffAdminState.select_to_country)
    await show_admin_tariff_to_countries(callback, page=0)
    await callback.answer()


@dp.callback_query(TariffAdminToCountryCallback.filter())
async def admin_select_to_country(
    callback: CallbackQuery, callback_data: TariffAdminToCountryCallback, state: FSMContext
):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.update_data(to_country_id=callback_data.id)
    await state.set_state(TariffAdminState.select_category)
    await show_admin_tariff_categories(callback, page=0)
    await callback.answer()


@dp.callback_query(TariffAdminCategoryCallback.filter())
async def admin_select_category(
    callback: CallbackQuery, callback_data: TariffAdminCategoryCallback, state: FSMContext
):
    if not is_admin_user(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.update_data(category_id=callback_data.id)
    await state.set_state(TariffAdminState.price)
    await callback.message.answer("Введите цену тарифа (например: 6.99$ за кг):")
    await callback.answer()


@dp.message(TariffAdminState.price, F.text)
async def admin_tariff_price(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await message.answer("Нет доступа")
        return
    await state.update_data(price=message.text.strip())
    await state.set_state(TariffAdminState.delivery_ru)
    await message.answer(
        "Введите срок доставки на русском (например: Доставка в течение: 2-10 дней со дня отгрузки):"
    )


@dp.message(TariffAdminState.delivery_ru, F.text)
async def admin_tariff_delivery_ru(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await message.answer("Нет доступа")
        return
    await state.update_data(delivery_text_ru=message.text.strip())
    await state.set_state(TariffAdminState.delivery_en)
    await message.answer(
        "Введите срок доставки на английском (например: Delivery time: 2-10 days from shipment date):"
    )


@dp.message(TariffAdminState.delivery_en, F.text)
async def admin_tariff_delivery_en(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await message.answer("Нет доступа")
        return
    await state.update_data(delivery_text_en=message.text.strip())
    await state.set_state(TariffAdminState.delivery_uz)
    await message.answer(
        "Введите срок доставки на узбекском (например: Yetkazib berish muddati: 2-10 kun, jo'natish kunidan boshlab):"
    )


@dp.message(TariffAdminState.delivery_uz, F.text)
async def admin_tariff_delivery_uz(message: Message, state: FSMContext):
    if not is_admin_user(message.from_user.id):
        await message.answer("Нет доступа")
        return
    data = await state.get_data()
    await state.update_data(delivery_text_uz=message.text.strip())

    async with session_maker() as session:
        await orm_add_tariff(
            session=session,
            from_country_id=data["from_country_id"],
            to_country_id=data["to_country_id"],
            category_id=data["category_id"],
            price=data["price"],
            delivery_text_ru=data["delivery_text_ru"],
            delivery_text_en=data["delivery_text_en"],
            delivery_text_uz=message.text.strip(),
        )

    await state.clear()
    await message.answer("Тариф добавлен ✅", reply_markup=build_admin_tariff_menu())


@dp.message(F.text == __("send-us"))
async def send_us(message: types.Message):
    await message.answer(
        _("sel_rec_coun"),
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text=_("send-us_rec-uz")),
                 types.KeyboardButton(text=_("send-us_rec-tjk"))],
                [types.KeyboardButton(text=_("trf-oth-cn"))],
                [types.KeyboardButton(text=_("❌ Отмена"))],
            ],
            resize_keyboard=True,
        ),
    )


@dp.message(F.text == __("Соц сети"))
async def send_link(message: types.Message):
    button1 = InlineKeyboardButton(
        text="Instagram",
        url="https://www.instagram.com/atlasexpress.usa?igsh=MW9nMGgxYjdqdzV3eA==",
    )
    button2 = InlineKeyboardButton(
        text="Facebook",
        url="https://www.facebook.com/profile.php?id=61561937198092&mibextid=LQQJ4d",
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[[button1], [button2]])
    await message.answer(_("Наши социальные сети"), reply_markup=markup)


from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import types

@dp.message(F.text == __("send-us_rec-uz"))
async def send_us_rec_uz_rate(message: types.Message):
    country = _("🇺🇸США → 🇺🇿УЗБ")
    standard = _("Стандарт")
    price = _("Цена стандарт США-Узб")
    deliver = _("Стандарт Сша-Узб Доставка")

    express = _("Экспресс")
    price2 = _("Цена экспресс сша-узб")
    deliver2 = _("Доставка экспресс сша-узб")

    express2 = _("экс-ком-сша-уз")
    price3 = _("экс-ком-цен-сша-уз")
    deliver3 = _("экс-ком-дост-сша-уз")

    ground = _("Ground-us-uz")
    ground_price = _("Цена эконом сша-узб")
    ground_deliver = _("Доставка эконом сша-узб")

    economy = _("send_usa-rec_uz_economy")
    economy_price = _("send_usa-rec_uz_economy_price")
    economy_deliver = _("send_usa-rec_uz_economy_deliver")

    async def send_rate(title, tariff, price, deliver, callback_name):
        text = f"{title}\n{tariff}\n{price}\n{deliver}\n"
        kb = InlineKeyboardBuilder()
        kb.button(text=_("Подробнее"), callback_data=callback_name)
        await message.answer(text, reply_markup=kb.as_markup())

    # Отправляем каждый тариф с кнопкой
    await send_rate(country, economy, economy_price, economy_deliver, "economy_send-usa_rec-uz")
    await send_rate(country, standard, price, deliver, "rate_standard_send-us_rec-uz")
    await send_rate(country, express, price2, deliver2, "rate_express_send-us_rec-uz")
    await send_rate(country, ground, ground_price, ground_deliver, "rate_ground_send-us_rec-uz")
    await send_rate(country, express2, price3, deliver3, "rate_express2_send-us_rec-uz")


@dp.callback_query(F.data == "economy_send-usa_rec-uz")
async def rate_standard_details(callback: types.CallbackQuery):
    await callback.message.answer(
        _("economy_send-usa_rec-uz")
    )
    await callback.answer()

@dp.callback_query(F.data == "rate_standard_send-us_rec-uz")
async def rate_standard_details(callback: types.CallbackQuery):
    await callback.message.answer(
        _("rate_standard_send-us_rec-uz")
    )
    await callback.answer()

@dp.callback_query(F.data == "rate_express_send-us_rec-uz")
async def rate_express_details(callback: types.CallbackQuery):
    await callback.message.answer(
        _("rate_express_send-us_rec-uz")
    )
    await callback.answer()

@dp.callback_query(F.data == "rate_ground_send-us_rec-uz")
async def rate_ground_details(callback: types.CallbackQuery):
    await callback.message.answer(
        _("rate_ground_send-us_rec-uz")
    )
    await callback.answer()

@dp.callback_query(F.data == "rate_express2_send-us_rec-uz")
async def rate_express2_details(callback: types.CallbackQuery):
    await callback.message.answer(
        _("rate_express2_send-us_rec-uz")
    )
    await callback.answer()

@dp.message(F.text == __("send-uz"))
async def send_uz_rates(message: types.Message):
    await message.answer(
        _("sel_rec_coun"),
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text=_("send-uz_rec-us")),
                 types.KeyboardButton(text=_("send-uz_rec-tjk")),],
                [types.KeyboardButton(text=_("trf-oth-cn"))],
                [types.KeyboardButton(text=_("❌ Отмена"))],
            ],
            resize_keyboard=True,
        ),
    )


@dp.message(F.text == __("send-uz_rec-us"))
async def send_uz_res_us(message: types.Message):
    country_uz = _("🇺🇿УЗБ → 🇺🇸США")

    # Тарифы
    standard = _("Стандарт")
    price_standard = _("price_uz_standart")
    deliver_standard = _("Доставка Узб-Сша Стандарт")

    express = _("Экспресс")
    price_express = _("Цена экспресс Узб-Сша")
    deliver_express = _("Доставка экспресс Узб-Сша")

    standart_commercial = _("standart_com_uz_usa")
    price_st_com = _("price_st_com_uz_usa")
    delivery_st_com = _("delivery_st_com_uz_usa")

    express_commercial = _("express_com_uz_usa")
    price_ex_com = _("price_ex_com_uz_usa")
    delivery_ex_com = _("delivery_ex_com_uz_usa")

    ground = _("Ground")
    price_ground = _("Эконом Узб-Сша")
    deliver_ground = _("Доставка эконом узб-сша")

    # Универсальная функция отправки тарифа с кнопкой
    async def send_rate(title, tariff, price, deliver, callback_name):
        text = f"{title}\n{tariff}\n{price}\n{deliver}\n"
        kb = InlineKeyboardBuilder()
        kb.button(text=_("Подробнее"), callback_data=callback_name)
        await message.answer(text, reply_markup=kb.as_markup())

    # Отправляем все тарифы
    await send_rate(country_uz, standard, price_standard, deliver_standard, "rate_standard_send-uz_rec-us")
    await send_rate(country_uz, express, price_express, deliver_express, "rate_express_send-uz_rec-us")
    await send_rate(country_uz, standart_commercial, price_st_com, delivery_st_com, "rate_standart_com_send-uz_rec-us")
    await send_rate(country_uz, express_commercial, price_ex_com, delivery_ex_com, "rate_express_com_send-uz_rec-us")
    await send_rate(country_uz, ground, price_ground, deliver_ground, "rate_ground_send-uz_rec-us")


# Подробные описания тарифов
@dp.callback_query(F.data == "rate_standard_send-uz_rec-us")
async def rate_standard_details(callback: types.CallbackQuery):
    await callback.message.answer(_("price_uz_standart_detail"))
    await callback.answer()

@dp.callback_query(F.data == "rate_express_send-uz_rec-us")
async def rate_express_details(callback: types.CallbackQuery):
    await callback.message.answer(_("rate_express_send-uz_rec-us"))
    await callback.answer()

@dp.callback_query(F.data == "rate_standart_com_send-uz_rec-us")
async def rate_standart_com_details(callback: types.CallbackQuery):
    await callback.message.answer(_("price_st_com_uz_usa_detail"))
    await callback.answer()

@dp.callback_query(F.data == "rate_express_com_send-uz_rec-us")
async def rate_express_com_details(callback: types.CallbackQuery):
    await callback.message.answer(_("price_ex_com_uz_usa_detail"))
    await callback.answer()

@dp.callback_query(F.data == "rate_ground_send-uz_rec-us")
async def rate_ground_details(callback: types.CallbackQuery):
    await callback.message.answer(_("rate_ground_send-uz_rec-us"))
    await callback.answer()

@dp.message(F.text == __("send-uz_rec-tjk"))
async def send_us_rec_uz_rate(message: types.Message):
    country = _("🇨🇦УЗБ → 🇺🇿ТЖК")
    standard = _("Стандарт")
    price = _("Цена стандарт Узб-Тжк")
    deliver = _("Стандарт Узб-Тжк Доставка")
    await message.answer(
        country + "\n" + standard + "\n" + price + "\n" + deliver + "\n\n"
    )

@dp.message(F.text == __("send-canada"))
async def send_us(message: types.Message):
    await message.answer(
        _("sel_rec_coun"),
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text=_("send-canada-accept-uz")),
                 types.KeyboardButton(text=_("send-canada-accept-tj"))],
                [types.KeyboardButton(text=_("trf-oth-cn"))],
                [types.KeyboardButton(text=_("❌ Отмена"))],
            ],
            resize_keyboard=True,
        ),
    )


@dp.message(F.text == __("send-tjk"))
async def send_us(message: types.Message):
    await message.answer(
        _("sel_rec_coun"),
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text=_("send-tjk-accept-usa")),],
                [types.KeyboardButton(text=_("trf-oth-cn"))],
                [types.KeyboardButton(text=_("❌ Отмена"))],
            ],
            resize_keyboard=True,
        ),
    )

@dp.message(F.text == __("send-canada-accept-uz"))
async def send_us_rec_uz_rate(message: types.Message):
    country = _("🇨🇦Канада → 🇺🇿УЗБ")
    standard = _("Стандарт")
    price = _("Цена стандарт Канада-Узб")
    deliver = _("Стандарт Канада-Узб Доставка")
    await message.answer(
        country + "\n" + standard + "\n" + price + "\n" + deliver + "\n\n"
    )

@dp.message(F.text == __("send-us_rec-tjk"))
async def send_us_rec_uz_rate(message: types.Message):
    country = _("🇺🇸США → 🇹🇯ТЖК")
    standard = _("Стандарт")
    price = _("Цена стандарт США-ТЖК")
    deliver = _("Стандарт США-ТЖК Доставка")
    await message.answer(
        country + "\n" + standard + "\n" + price + "\n" + deliver + "\n\n"
    )

@dp.message(F.text == __("send-canada-accept-tj"))
async def send_us_rec_uz_rate(message: types.Message):
    country = _("🇨🇦Канада → 🇹🇯ТЖК")
    standard = _("Стандарт")
    price = _("Цена стандарт Канада-ТЖК")
    deliver = _("Стандарт Канада-ТЖК Доставка")
    await message.answer(
        country + "\n" + standard + "\n" + price + "\n" + deliver + "\n\n"
    )

@dp.message(F.text == __("trf-oth-cn"))
async def other_countries(message: types.Message):
    await message.answer(_("Следите за новостями скоро будут и в других странах"))


@dp.message(F.text == __("Информация"))
async def other_countries(message: types.Message):
    await message.answer("https://t.me/AtlasExpressUS")


@dp.message(F.text == __("Статистика бота"))
async def other_countries(message: types.Message):
    await message.answer("Временно недоступен!")


@dp.message(Menu.main_menu, F.text == __("Поддержка"))
async def check_id(message: types.Message, state: FSMContext) -> None:
    await message.answer(
        _("Пожалуйста, напишите ваше сообщение!"),
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text=_("❌ Отмена"))]], resize_keyboard=True
        ),
    )
    await state.set_state(Menu.send_msg)


@dp.message(Menu.send_msg)
async def id_typed(
    message: types.Message,
):
    if message.text:
        await bot.send_message(
            group_id,
            f"{message.from_user.first_name} | ({message.from_user.id})\n\n{message.text}",
        )
        await message.answer(
            _("Ваше сообщение передано, ждите ответ от службы поддержки клиентов!")
        )


@dp.message(F.text)
async def id_typed(
    message: types.Message,
):
    # await bot.send_message(960217500, f"{message.chat.id}")
    if message.chat.id == group_id and message.reply_to_message:
        response = message.reply_to_message.text.split("\n\n")
        response2 = response[0].split(" | (")
        await bot.send_message(
            response2[1][:-1], _("Сообщение от поддержки!") + "\n\n" + str(message.text)
        )



last_sent_id = 0
API_URL = "https://atlasexpress.uz/ru/api/broadcast/latest/"
LAST_ID_FILE = "last_broadcast_id.txt"

try:
    with open(LAST_ID_FILE, "r") as f:
        last_sent_id = int(f.read())
except:
    last_sent_id = 0


async def send_new_broadcast():
    global last_sent_id

    async with aiohttp.ClientSession() as client:
        async with client.get(API_URL) as resp:
            if resp.status != 200:
                print(f"Ошибка запроса API: {resp.status}")
                return

            data = await resp.json()
            new_id = data["id"]

            if new_id <= last_sent_id:
                return  # ничего нового нет

            # Получаем всех активных пользователей
            async with session_maker() as session:
                result = await session.execute(select(User).where(User.is_active == True))
                users = result.scalars().all()

            # 📸 Скачиваем медиа (фото и видео)
            temp_files = []
            media_files = []

            # Поддержка фото
            for key in ["image1", "image2", "image3"]:
                url = data.get(key)
                if url:
                    try:
                        async with client.get(url) as img_resp:
                            if img_resp.status == 200:
                                tmp = NamedTemporaryFile(delete=False, suffix=".jpg")
                                tmp.write(await img_resp.read())
                                tmp.flush()
                                temp_files.append(tmp)
                                media_files.append(("photo", tmp))
                    except Exception as e:
                        print(f"Ошибка скачивания {url}: {e}")

            # Поддержка видео 🎥
            for key in ["video", "video1", "video2"]:
                url = data.get(key)
                if url:
                    try:
                        async with client.get(url) as vid_resp:
                            if vid_resp.status == 200:
                                # Определяем MIME тип из заголовка
                                content_type = vid_resp.headers.get("Content-Type", "")
                                print(f"[DEBUG] {url} -> Content-Type: {content_type}")

                                # Если сервер не дал Content-Type — пробуем по расширению
                                if not content_type or content_type == "application/octet-stream":
                                    guessed_type, _ = mimetypes.guess_type(url)
                                    if guessed_type:
                                        content_type = guessed_type

                                # Если MIME указывает на видео
                                if "video" in content_type:
                                    suffix = ".mp4"
                                    tmp = NamedTemporaryFile(delete=False, suffix=suffix)
                                    tmp.write(await vid_resp.read())
                                    tmp.flush()
                                    temp_files.append(tmp)
                                    media_files.append(("video", tmp))
                                else:
                                    # Если вдруг пришёл thumbnail или изображение
                                    suffix = ".jpg"
                                    tmp = NamedTemporaryFile(delete=False, suffix=suffix)
                                    tmp.write(await vid_resp.read())
                                    tmp.flush()
                                    temp_files.append(tmp)
                                    media_files.append(("photo", tmp))
                    except Exception as e:
                        print(f"Ошибка скачивания {url}: {e}")

            # 📤 Отправка пользователям
            for user in users:
                try:
                    if not media_files:
                        # Только текст
                        await bot.send_message(chat_id=user.user_id, text=data["description"])

                    elif len(media_files) == 1:
                        mtype, f = media_files[0]

                        if mtype == "video":
                            await bot.send_video(chat_id=user.user_id, video=FSInputFile(f.name),
                                                 caption=data["description"])

                        elif mtype == "photo":
                            await bot.send_photo(chat_id=user.user_id, photo=FSInputFile(f.name),
                                                 caption=data["description"])

                    else:
                        # Альбом из фото и/или видео
                        media_group = []
                        for i, (mtype, f) in enumerate(media_files):
                            if mtype == "photo":
                                media = InputMediaPhoto(media=FSInputFile(f.name),
                                                        caption=data["description"] if i == 0 else None)
                            else:
                                media = InputMediaVideo(media=FSInputFile(f.name),
                                                        caption=data["description"] if i == 0 else None)
                            media_group.append(media)
                        await bot.send_media_group(chat_id=user.user_id, media=media_group)

                except Exception as e:
                    print(f"Ошибка отправки пользователю {user.user_id}: {e}")

            # 🧹 Удаляем временные файлы
            for f in temp_files:
                try:
                    f.close()
                    os.unlink(f.name)
                except Exception as e:
                    print(f"Ошибка удаления временного файла {f.name}: {e}")

            # Сохраняем новый ID
            last_sent_id = new_id
            with open(LAST_ID_FILE, "w") as f:
                f.write(str(last_sent_id))


async def broadcast_loop():
    while True:
        try:
            await send_new_broadcast()
        except Exception as e:
            print("Ошибка в broadcast_loop:", e)
        await asyncio.sleep(10)



async def on_start_app(dispatcher):
    asyncio.create_task(broadcast_loop())
    await create_db()


async def main() -> None:
    dp.startup.register(on_start_app)

    dp.update.middleware(DataBaseSession(session_pool=session_maker))
    i18n = I18n(path="locales", default_locale="ru", domain="messages")
    dp.message.outer_middleware(FSMI18nMiddleware(i18n=i18n))
    dp.callback_query.outer_middleware(FSMI18nMiddleware(i18n=i18n))

    # await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
