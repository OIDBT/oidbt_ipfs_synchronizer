import asyncio


async def run_example():
    import urllib.request

    import oidbt_bangumi_ani_getter.log
    from oidbt_bangumi_ani_getter import Bangumi_ani_getter
    from oidbt_bt_entry_getter import Mikan_bt_entry_getter

    from .ipfs_synchronizer import Ipfs_synchronizer
    from .log import log

    oidbt_bangumi_ani_getter.log.log.print_level = (
        oidbt_bangumi_ani_getter.log.log.LogLevel.info
    )

    log.print_level = log.LogLevel.debug

    proxies: dict[str, str] = urllib.request.getproxies()
    proxy_url = proxies.get("http")
    if proxy_url is not None:
        proxy_url = proxy_url.split("//", maxsplit=1)[-1].strip()
        proxy_url = f"socks5://{proxy_url}"

    DATABASE_FILENAME = "OIDBT_SQLite"

    bangumi_ani_getter = Bangumi_ani_getter(
        database_filename=DATABASE_FILENAME,
        proxy=proxy_url,
    )

    bt_entry_getter_list = [
        Mikan_bt_entry_getter(
            database_filename=DATABASE_FILENAME,
            proxy=proxy_url,
        ),
    ]

    ipfs_synchronizer = Ipfs_synchronizer(
        database_filename=DATABASE_FILENAME,
        bt_entry_getter_list=bt_entry_getter_list,
    )

    await asyncio.gather(
        bangumi_ani_getter.auto_req(),
        *(
            bt_entry_getter.auto_req(
                bgm_ani_all_data_getter=bangumi_ani_getter.get_all_data
            )
            for bt_entry_getter in bt_entry_getter_list
        ),
        ipfs_synchronizer.auto_sync(),
    )


if __name__ == "__main__":
    asyncio.run(run_example())
