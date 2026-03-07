import asyncio


async def run_example():
    import json
    import urllib.request
    from pathlib import Path
    from typing import NotRequired, TypedDict

    from oidbt_bangumi_ani_getter import Bangumi_ani_getter
    from oidbt_bt_entry_getter import Mikan_bt_entry_getter
    from prompt_toolkit.shortcuts import PromptSession

    from .ipfs_synchronizer import Ipfs_synchronizer
    from .log import log

    log.print_level = log.LogLevel.debug
    log.write_level = log.LogLevel.error
    log.html_filename = "OIDBT-log.html"

    class Config(TypedDict):
        cookies: NotRequired[dict[str, dict[str, str]]]
        email: NotRequired[str]

    config: Config = (
        json.loads(config_path.read_text("utf-8"))
        if (config_path := Path("config.json")).is_file()
        else {}
    )

    proxies: dict[str, str] = urllib.request.getproxies()
    proxy_url = proxies.get("http")
    if proxy_url is not None:
        proxy_url = proxy_url.split("//", maxsplit=1)[-1].strip()
        proxy_url = f"socks5://{proxy_url}"

    DATABASE_FILENAME = "OIDBT_SQLite"

    bangumi_ani_getter = Bangumi_ani_getter(
        database_filename=DATABASE_FILENAME,
        proxy=proxy_url,
        cookies=config.get("cookies", {}).get("bgm"),
        email=config.get("email"),
    )

    bt_entry_getter_list = [
        Mikan_bt_entry_getter(
            database_filename=DATABASE_FILENAME,
            proxy=proxy_url,
            cookies=config.get("cookies", {}).get("mikan"),
            email=config.get("email"),
        ),
    ]

    ipfs_synchronizer = Ipfs_synchronizer(
        database_filename=DATABASE_FILENAME,
        bt_entry_getter_list=bt_entry_getter_list,
    )

    async def input_async() -> None:
        session = PromptSession()
        while True:
            cmd = None
            try:
                cmd = await session.prompt_async("动态调试> ")
                exec(cmd)
            except Exception as e:
                log.error("{} {}", cmd, e, deep=True)
            except KeyboardInterrupt:
                continue

    await asyncio.gather(
        bangumi_ani_getter.auto_req(),
        *(
            bt_entry_getter.auto_req(
                bgm_ani_all_data_getter=bangumi_ani_getter.get_all_data
            )
            for bt_entry_getter in bt_entry_getter_list
        ),
        ipfs_synchronizer.auto_sync(),
        input_async(),
    )


if __name__ == "__main__":
    asyncio.run(run_example())
