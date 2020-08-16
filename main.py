# Base
import asyncio
import re
import urllib
from pathlib import Path
from typing import Tuple

# Installed
import aiohttp
import aiofiles
import mutagen
from bs4 import BeautifulSoup
import logzero
from logzero import logger

BASE_URL = "https://syair.info"
MAX_SIMULTANEOUS_REQUESTS = 10
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux i686; rv:48.0) Gecko/20100101 Firefox/48.0",
                "Accept-encoding": "gzip"}
LIBRARY_PATH = "D:/Music"


async def download_all_lyrics():
    root_path = Path(LIBRARY_PATH)

    async_tasks = []
    semaphore = asyncio.Semaphore(MAX_SIMULTANEOUS_REQUESTS)

    for supported_type in ("*.mp3", "*.flac"):
        for path in root_path.rglob(supported_type):
            logger.debug(f"Found '{path}'")
            task = asyncio.create_task(download_lyrics(semaphore, path))
            async_tasks.append(task)

    return await asyncio.gather(*async_tasks)


async def download_lyrics(semaphore: asyncio.Semaphore, file: Path):
    async with semaphore:
        logger.info(f"Downloading lyrics for '{file}'")

        tags = await read_tags_from_file(file)
        if tags[0] is None or tags[1] is None:
            logger.error(f"Aborted downloading '{file}'")
            return

        search_url = make_search_url(tags[0], tags[1])
        search_soup = await download_url(search_url)
        lyrics_link = search_soup.find("a", href=True, class_="title")['href']
        logger.debug(f"Found lyrics link '{lyrics_link}'")

        lyrics_url = BASE_URL + lyrics_link
        logger.debug(f"Generated link URL '{lyrics_url}'")
        lyrics_soup = await download_url(lyrics_url)
        lrc_file_link = lyrics_soup.find("span", text=re.compile(r".*\.lrc")).parent['href']
        logger.debug(f"Found LRC file link '{lrc_file_link}'")

        lrc_file_url = BASE_URL + lrc_file_link
        logger.debug(f"Generated LRC link URL '{lrc_file_url}'")
        lrc_file_path = file.with_suffix('.lrc')
        logger.debug(f"Destination file '{lrc_file_path}'")
        await download_file(lrc_file_url, lrc_file_path)

        logger.info(f"Finished downloading '{lrc_file_path}'")


async def read_tags_from_file(file: str) -> Tuple[str, str]:
    try:
        tags = mutagen.File(file)

        if type(tags) is mutagen.mp3.MP3:
            try:
                artist = tags.tags.getall('TPE1')[0].text[0]
                title = tags.tags.getall('TIT2')[0].text[0]
                logger.debug(f"Loaded MP3 file '{file}': '{artist} - {title}'")
                return artist, title
            except IndexError as e:
                logger.exception(e)
                pass
        elif type(tags) is mutagen.flac.FLAC:
            try:
                artist = tags['artist'][0]
                title = tags['title'][0]
                logger.debug(f"Loaded FLAC file '{file}': '{artist} - {title}'")
                return artist, title
            except IndexError as e:
                logger.exception(e)
                pass
        else:
            logger.debug(f"Loaded unsupported file '{file}': '{tags}'")

    except mutagen.MutagenError as e:
        logger.exception(e)

    return None, None



def make_search_url(artist: str, title: str) -> str:
    search_url = BASE_URL + "/search?q=" + urllib.parse.quote_plus(artist + " " + title)
    logger.debug(f"Generated search URL '{search_url}'")
    return search_url


async def download_url(url: str):
    logger.debug(f"Downloading HTML at '{url}'")

    async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
        async with session.get(url) as response:
            html_content = await response.text('latin-1')
            return BeautifulSoup(html_content, "html.parser")


async def download_file(url: str, destination: str):
    logger.debug(f"Downloading file at '{url}' in '{destination}'")

    async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
        async with session.get(url) as response:
            f = await aiofiles.open(destination, mode='wb')
            file_content = await response.read()
            await f.write(file_content)
            await f.close()


if __name__ == "__main__":
    import asyncio
    import cProfile
    import pstats

    # Setup Logging
    # logzero.logfile("logs/logfile.log", maxBytes=1e9, backupCount=1)
    logzero.loglevel(level=20)  # logging.INFO
    # logzero.loglevel(level=10)  # logging.DEBUG

    # Start Profiler
    pr = cProfile.Profile()
    pr.enable()

    # Download all lyrics
    loop = asyncio.get_event_loop()
    loop.run_until_complete(download_all_lyrics())

    # Stop profiler and print stats
    pr.disable()
    ps = pstats.Stats(pr)
    ps.sort_stats('cumulative').print_stats()
