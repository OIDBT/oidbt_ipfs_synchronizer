import asyncio
import datetime
import itertools
import json
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Literal, NoReturn

import httpx
import pydantic
import zstandard
from pydantic import BaseModel, ValidationError, field_serializer
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import create_engine

from .log import log

if TYPE_CHECKING:
    from collections.abc import Iterable

    from oidbt_bt_entry_getter import Base_bt_entry_getter


class Ipfs_synchronizer:
    DATABASE_LOCK: ClassVar = asyncio.Lock()
    ROOT_DIR: ClassVar = Path("oidbt_ipfs_root")
    ZSTD_LEVEL: ClassVar = 22
    IPNS_PARAMS: ClassVar = {
        "key": "self",
        "lifetime": "8760h",
        "ttl": "5m",
    }

    @classmethod
    def enzstd(cls, data: bytes) -> bytes:
        return zstandard.compress(data, level=cls.ZSTD_LEVEL)

    @classmethod
    def dezstd(cls, data: bytes) -> bytes:
        return zstandard.decompress(data)

    def __init__(
        self,
        *,
        database_filename: str,
        bt_entry_getter_list: Iterable[Base_bt_entry_getter],
        proxy: httpx._types.ProxyTypes | None = None,
        timeout: httpx._types.TimeoutTypes = 600,
    ) -> None:
        self.client = httpx.AsyncClient(
            http2=True,
            proxy=proxy,
            timeout=timeout,
        )
        """HTTP Client"""

        if not database_filename.endswith(".db"):
            database_filename += ".db"
        self.database_filename = database_filename
        self.sync_engine = create_engine(f"sqlite:///{database_filename}")
        """同步 database engine"""
        self.async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_filename}"
        )
        """异步 database engine"""

        self.bt_entry_getter_list = bt_entry_getter_list

    def __del__(self) -> None:
        try:
            asyncio.get_running_loop()
            asyncio.create_task(self.client.aclose())  # noqa: RUF006
        except RuntimeError:
            asyncio.run(self.client.aclose())

    async def sync_bgm_files(self) -> tuple[tuple[str, bytes], ...]:
        """遍历 BT Entry 的数据库，更新以 Bangumi ID 为键的目录文件"""

        class File_content(BaseModel):
            class Magnet_item(BaseModel):
                magnet: str
                source_link_set: set[str]  # 一定有来源，所以不设置空默认值
                title_set: set[str]
                cid: str | None = pydantic.Field(
                    default=None, description="该目录文件的 IPFS 源 CID"
                )
                """保留字段，暂无 IPFS 源"""

            update_time: datetime.datetime = pydantic.Field(
                default_factory=lambda: datetime.datetime.now().astimezone()
            )
            magnet_list: list[Magnet_item] = pydantic.Field(default_factory=list)

            @field_serializer("update_time")
            def serialize_datetime(self, dt: datetime.datetime) -> str:
                return dt.isoformat(sep=" ", timespec="minutes")

        # 从数据库读取数据并处理
        bgm_id__file_content: dict[int, File_content] = {}
        for bt_entry_getter in self.bt_entry_getter_list:
            page_link_head = bt_entry_getter.page_link_head
            for website_entry_data in await bt_entry_getter.get_all_data():
                if (
                    not website_entry_data.magnet
                    or not website_entry_data.match_id_list
                ):
                    continue
                magnet = website_entry_data.magnet.decode()
                source_link = page_link_head + website_entry_data.page_link_point
                source_title = website_entry_data.title

                # 只取匹配度最高的 ID (第一个 ID)
                match_id = website_entry_data.match_id_list[0]

                if match_id not in bgm_id__file_content:
                    bgm_id__file_content[match_id] = File_content()
                file_content = bgm_id__file_content[match_id]

                magnet_list = file_content.magnet_list
                for magnet_item in magnet_list:
                    if magnet_item.magnet == magnet:
                        magnet_item.source_link_set.add(source_link)
                        magnet_item.title_set.add(source_title)
                        break
                else:
                    magnet_list.append(
                        File_content.Magnet_item(
                            magnet=magnet,
                            source_link_set={source_link},
                            title_set={source_title},
                        )
                    )

        # 生成返回值
        root_path: Final = str(self.ROOT_DIR / "bangumi").replace("\\", "/")
        return tuple(
            (
                f"{root_path}/{i}",
                self.enzstd(v.model_dump_json(exclude_none=True).encode()),
            )
            for i, v in bgm_id__file_content.items()
        )

    async def vacuum_db(self) -> bytes:
        async with (
            self.DATABASE_LOCK,
            self.async_engine.connect() as conn,
        ):
            await conn.exec_driver_sql("VACUUM")
            await conn.commit()

        return Path(self.database_filename).read_bytes()

    async def sync_ipfs(
        self,
        *,
        bgm_files: tuple[tuple[str, bytes], ...],
        db_file: bytes,
    ) -> str:
        """:return: CID"""

        class Add_res_item(BaseModel):
            Name: str
            Hash: str
            Size: int

        while True:
            try:
                response = await self.client.post(
                    "http://127.0.0.1:5001/api/v0/add",
                    params={"recursive": True, "wrap-with-directory": True},
                    files=tuple[tuple[str, tuple[str, bytes]]](
                        ("file", (filename, content))
                        for filename, content in itertools.chain(
                            bgm_files,
                            [(f"{self.ROOT_DIR}/{self.database_filename}", db_file)],
                        )
                    ),
                )
                log.debug(
                    "{} 请求头: {}",
                    self.__class__.__name__,
                    response.request.headers,
                    print_level=log.LogLevel._detail,
                )
                response.raise_for_status()
                log.debug(
                    "{} 响应头: {} {} {}",
                    self.__class__.__name__,
                    response.http_version,
                    response.status_code,
                    response.headers,
                    print_level=log.LogLevel._detail,
                )

                res_list = response.text.splitlines()
                last_res = Add_res_item(**json.loads(res_list[-1]))
                log.info(
                    "{} api/v0/add 响应: len={}   [-1]={}",
                    self.__class__.__name__,
                    len(res_list),
                    last_res.model_dump(),
                )

            except httpx.HTTPStatusError as e:
                log.error(
                    "{} 状态码错误: {} {}",
                    self.__class__.__name__,
                    e.response.status_code,
                    e.response.text,
                )
            except httpx.ConnectError as e:
                log.error("{} 连接失败: {}", self.__class__.__name__, e)
            except httpx.TimeoutException:
                log.warning("{} 请求超时", self.__class__.__name__)
            except ValidationError as e:
                log.error("{} 类型错误: {}", self.__class__.__name__, e)
                raise

            else:
                return last_res.Hash

            await asyncio.sleep(1)

    async def sync_ipns(self, *, cid: str):
        class Name_publish_item(BaseModel):
            Name: str
            Value: str

        while True:
            try:
                response = await self.client.post(
                    "http://127.0.0.1:5001/api/v0/name/publish",
                    params={"arg": cid, **self.IPNS_PARAMS},
                )
                log.debug(
                    "{} 请求头: {}",
                    self.__class__.__name__,
                    response.request.headers,
                    print_level=log.LogLevel._detail,
                )
                response.raise_for_status()
                log.debug(
                    "{} 响应头: {} {} {}",
                    self.__class__.__name__,
                    response.http_version,
                    response.status_code,
                    response.headers,
                    print_level=log.LogLevel._detail,
                )

                res = Name_publish_item(**json.loads(response.text))
                log.debug(
                    "{} api/v0/name/publish 响应: {}",
                    self.__class__.__name__,
                    res.model_dump(),
                )

            except httpx.HTTPStatusError as e:
                log.error(
                    "{} 状态码错误: {} {}",
                    self.__class__.__name__,
                    e.response.status_code,
                    e.response.text,
                )
            except httpx.ConnectError as e:
                log.error("{} 连接失败: {}", self.__class__.__name__, e)
            except httpx.TimeoutException:
                log.warning("{} 请求超时", self.__class__.__name__)
            except ValidationError as e:
                log.error("{} 类型错误: {}", self.__class__.__name__, e)
                raise

            else:
                return res.Name

            await asyncio.sleep(1)

    async def auto_sync(self) -> NoReturn:
        """自动同步"""
        cycle_num: int = 1
        sleep_time: Literal[60, 600] = 60
        while True:
            await asyncio.sleep(sleep_time)

            log.info("{} 第 {} 次同步", self.__class__.__name__, cycle_num)

            bgm_files = await self.sync_bgm_files()
            db_file = await self.vacuum_db()
            cid = await self.sync_ipfs(
                bgm_files=bgm_files,
                db_file=db_file,
            )
            ipns_name = await self.sync_ipns(cid=cid)

            log.info("IPNS Name = {}", ipns_name)

            cycle_num += 1
            sleep_time = 600
