#!/usr/bin/env python3
import asyncio
import os
import sys

import httpx

_crypto_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ScrollPack-crypto")
if os.path.isdir(_crypto_path):
    sys.path.insert(0, _crypto_path)

from novel_packer import NovelPacker
from pack_argument import PackArgument
from config import AppConfig
from logger import logger
from version import VERSION

GIT_URL = "https://github.com/Montaro2017/bili_novel_packer"


def print_welcome():
    print("欢迎使用轻小说/漫画打包器!")
    print(f"作者: Spark  当前版本: {VERSION}")
    print("支持: 哔哩轻小说 | 轻小说文库 | 拷贝漫画 | 包子漫画")
    print()
    print("[1] 输入链接开始打包")
    print("[2] 设置")
    print("[3] 测试连接")
    print()


def read_url():
    while True:
        print("请输入URL(支持哔哩轻小说&轻小说文库&拷贝漫画):")
        url = sys.stdin.readline().strip()
        if url:
            return url.split(" ")[0]


def print_novel_detail(novel):
    print()
    print(novel)


def read_pack_argument(catalog, is_manga=False):
    arg = PackArgument()
    selected = read_select_volume(catalog)
    arg.pack_volumes = selected

    if is_manga:
        return arg

    if len(arg.pack_volumes) > 1:
        ans = input("是否合并选择的分卷为一个文件? (y/n): ").strip().lower()
        arg.combine_volume = ans == "y"

    ans = input("是否在每章开头添加章节标题? (y/n): ").strip().lower()
    arg.add_chapter_title = ans != "n"
    return arg


def read_select_volume(catalog):
    for i, v in enumerate(catalog.volumes):
        ch_count = len(v.chapters)
        print(f"[{i + 1}] {v.volume_name} ({ch_count}话)" if ch_count else f"[{i + 1}] {v.volume_name}")
    print()
    print("[0] 选择全部")
    print("请选择需要下载的分卷(可输入如1-9进行范围选择以及如2,5单独选择):")
    inp = sys.stdin.readline().strip()

    if not inp or inp == "0":
        return list(catalog.volumes)

    inp = inp.replace("，", ",").replace(" ", ",")
    selected = []
    for part in inp.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            rng = part.split("-")
            a, b = int(rng[0]), int(rng[1])
            if a > b:
                a, b = b, a
            for i in range(a, b + 1):
                if 1 <= i <= len(catalog.volumes):
                    selected.append(catalog.volumes[i - 1])
        else:
            i = int(part)
            if 1 <= i <= len(catalog.volumes):
                selected.append(catalog.volumes[i - 1])
    return selected


async def do_pack():
    url = read_url()
    logger.info(f"version: {VERSION}")
    logger.info(f"URL: {url}")
    packer = NovelPacker.from_url(url)
    print("正在加载数据...")
    await packer.init()
    logger.info(packer.novel)
    print_novel_detail(packer.novel)
    arg = read_pack_argument(packer.catalog, is_manga=packer._is_manga)
    logger.info(arg)
    await packer.pack(arg)
    await packer.close()
    print("全部任务已完成，按回车键继续.")
    input()


def show_settings():
    cfg = AppConfig()
    while True:
        print()
        print("[设置]")
        print(f"[1] 无头模式(后台运行): {'是' if cfg.headless else '否 (可见窗口)'}")
        print(f"[2] 输出目录: {cfg.output_dir or '.'}")
        print(f"[3] 最大重试次数: {cfg.max_retries}")
        print(f"[4] 重试间隔(秒): {cfg.retry_delay_seconds}")
        if cfg.has_proxy:
            print(f"[5] 代理: {cfg.proxy_host}:{cfg.proxy_port}")
        else:
            print(f"[5] 代理: 未设置")
        print("[0] 返回")
        print()
        choice = input("选择要修改的项: ").strip()

        if choice == "0":
            cfg.save()
            break
        elif choice == "1":
            cfg.headless = not cfg.headless
            print(f"已切换为: {'无头模式' if cfg.headless else '可见窗口'}")
            cfg.save()
        elif choice == "2":
            path = input("输入输出目录: ").strip()
            if path:
                cfg.output_dir = path
                cfg.save()
                print(f"输出目录已设为: {path}")
        elif choice == "3":
            try:
                cfg.max_retries = int(input("输入最大重试次数: ").strip())
                cfg.save()
            except ValueError:
                print("无效数字")
        elif choice == "4":
            try:
                cfg.retry_delay_seconds = int(input("输入重试间隔(秒): ").strip())
                cfg.save()
            except ValueError:
                print("无效数字")
        elif choice == "5":
            host = input("代理地址 (留空取消): ").strip()
            if host:
                cfg.proxy_host = host
                try:
                    cfg.proxy_port = int(input("代理端口: ").strip())
                except ValueError:
                    cfg.proxy_port = 1080
                cfg.proxy_username = input("用户名 (无则留空): ").strip() or None
                cfg.proxy_password = input("密码 (无则留空): ").strip() or None
            else:
                cfg.proxy_host = None
                cfg.proxy_port = None
                cfg.proxy_username = None
                cfg.proxy_password = None
            cfg.save()
            print("代理已更新")


async def do_test():
    print("\n测试连接...")
    targets = [
        ("哔哩轻小说", "https://www.bilinovel.com/"),
    ]
    for name, url in targets:
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True)
            ok = r.status_code < 400
            print(f"  [{ 'OK' if ok else 'FAIL' }] {name} ({url}) -> {r.status_code}")
        except Exception as e:
            print(f"  [FAIL] {name} ({url}) -> {e}")

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            await browser.close()
        print("  [OK] Playwright 浏览器引擎")
    except Exception as e:
        print(f"  [FAIL] Playwright -> {e} (运行: playwright install chromium)")

    print()
    input("按回车键继续.")


async def main_loop():
    while True:
        print_welcome()
        choice = input("请输入选项: ").strip()
        if choice == "1":
            await do_pack()
        elif choice == "2":
            show_settings()
        elif choice == "3":
            await do_test()
        elif choice.lower() == "q":
            print("再见!")
            sys.exit(0)
        else:
            print("无效选项，输入 1/2/3/q")


def main():
    if "--visible" in sys.argv:
        AppConfig().headless = False
    if "--test" in sys.argv:
        asyncio.run(do_test())
        return
    if "--settings" in sys.argv:
        show_settings()
        return
    try:
        asyncio.run(main_loop())
    except Exception as e:
        logger.error(e)
        print(e)
        print(f"运行出错，按回车键退出.({VERSION})")
        input()


if __name__ == "__main__":
    main()
