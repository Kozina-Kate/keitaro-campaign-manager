"""Проверка API без изменения данных и без вывода секретов."""

import asyncio

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError


async def check():
    client = KeitaroClient(Settings())
    try:
        data = await client.reference_data()
        for resource in type(data).model_fields:
            print(f"{resource}: {len(getattr(data, resource))}")
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(check())
    except KeitaroError as error:
        print(f"Ошибка [{error.code}]: {error}")
        raise SystemExit(1) from None
