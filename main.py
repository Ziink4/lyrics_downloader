"""
reads your music library and download synchronized lyrics (as .lrc files)
"""
# Base
import asyncio
import re
import urllib
from pathlib import Path
from typing import Tuple, Optional

# Installed
import aiofiles
import aiohttp
import logzero
import mutagen
from bs4 import BeautifulSoup
from logzero import logger

BASE_URL = "https://www.lyricsify.com"
MAX_SIMULTANEOUS_REQUESTS = 10
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/96.0.4664.110 Safari/537.36",
                "Upgrade-Insecure-Requests": "1",
                "Accept-encoding": "gzip, deflate"}

# Specify your music library location here :
LIBRARY_PATH = "D:/Music"


async def download_all_lyrics():
    """
    Recursively browse your music library and download the associated lyrics.

    Lyrics files will be downloaded in the same location as the music files
    and will have the same file name but with the ".lrc" extension

    Currently, supported file formats are MP3, FLAC, Ogg (Opus) and MP4/M4A
    """
    root_path = Path(LIBRARY_PATH)

    async_tasks = []
    semaphore = asyncio.Semaphore(MAX_SIMULTANEOUS_REQUESTS)

    for path in root_path.rglob("*.*"):
        logger.debug(f"Found '{path}'")
        task = asyncio.create_task(download_lyrics(semaphore, path))
        async_tasks.append(task)

    return await asyncio.gather(*async_tasks)


async def download_lyrics(semaphore: asyncio.Semaphore, file: Path) -> None:
    """
    Download the lyrics for the specified music file

    The process goes as follows :
    1) Skip the current file if the lyrics are already present
    2) Read the embedded tags of the music file to extract artist and title information
    3) Generate the URL and download the web page for the search results on https://syair.info
    4) Generate the URL and download the web page for the first result of the search
    5) Generate the URL and download the final .lrc file

    :param semaphore: A semaphore object to limit the number of concurrent downloads
    :param file: The input music file
    """
    logger.debug(f"Downloading lyrics for '{file}'")

    # 1) Skip everything if we are parsing a lyrics file
    if file.suffix == '.lrc':
        logger.debug(f"Skipping lyrics file '{file}'")
        return

    # 2) Skip the current file if the lyrics are already present
    lrc_file_path = file.with_suffix('.lrc')
    logger.debug(f"Destination file '{lrc_file_path}'")
    if lrc_file_path.exists():
        async with aiofiles.open(lrc_file_path, mode='rb') as f:
            start_line = await f.readline()

        if b'[' not in start_line or b']' not in start_line:
            logger.error(f"Corrupted LRC file '{lrc_file_path}', cleaning it up")
            lrc_file_path.unlink()
        else:
            logger.debug(f"Skipping existing file '{lrc_file_path}'")
            return

    # 3) Read the embedded tags of the music file to extract artist and title information
    tags = read_tags_from_file(file)
    if tags is None:
        logger.error(f"Aborted downloading '{file}' (Unsupported format)")
        return

    async with semaphore, aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
        # 4) Generate the URL and download the web page for the search results
        artist, _, _, title = tags
        search_url = make_search_url(artist, title)
        search_soup = await download_url(session, search_url)

        # 5) Generate the URL and download the web page for the first result of the search
        lyrics_tag = search_soup.find("a", href=True, class_="title")
        if lyrics_tag is None:
            logger.error(f"Aborted downloading '{file}' (Could not find lyrics file)")
            return

        lyrics_link = lyrics_tag['href']
        logger.debug(f"Found lyrics link '{lyrics_link}'")
        lyrics_url = BASE_URL + lyrics_link
        logger.debug(f"Generated link URL '{lyrics_url}'")
        lyrics_soup = await download_url(session, lyrics_url)

        # 6) Generate the URL for the download page
        lrc_download_link = lyrics_soup.find("span", text=re.compile(r".*\.lrc")).parent['href']
        logger.debug(f"Found LRC download page URL '{lrc_download_link}'")
        lyrics_file_soup = await download_url(session, lrc_download_link)

        # 7) Generate the final URL
        lrc_file_link = lyrics_file_soup.find("a", text="click here")['href']
        logger.debug(f"Found LRC download URL '{lrc_file_link}'")
        await download_file(session, lrc_file_link, lrc_file_path)

    logger.info(f"Finished downloading '{lrc_file_path}'")


def read_tags_from_file(file: Path) -> Optional[Tuple[str, str, int, str]]:
    """
    Read the artist and title information from a music file
    Currently supported file formats are .mp3 and .flac
    :param file: Input music file
    :return: Artist name and title as strings
    """
    try:
        logger.debug(f"Trying to load '{file}'")

        tags = mutagen.File(file)

        match type(tags):
            case mutagen.mp3.MP3:
                try:
                    artist = tags.tags.getall('TPE1')[0].text[0]
                except IndexError as e:
                    logger.exception(e)
                    artist = ''

                try:
                    album = tags.tags.getall('TALB')[0].text[0]
                except IndexError as e:
                    logger.exception(e)
                    album = ''

                try:
                    track = int(re.match(r"(\d+)(?:/\d+)?", tags.tags.getall('TRCK')[0].text[0])[1])
                except IndexError as e:
                    logger.exception(e)
                    track = 0

                title = tags.tags.getall('TIT2')[0].text[0]
                logger.debug(f"Loaded MP3 file: '{artist} / {album} / {track} - {title}'")
                return artist, album, track, title

            case mutagen.flac.FLAC | mutagen.oggopus.OggOpus:
                try:
                    artist = tags['artist'][0]
                except (KeyError, IndexError) as e:
                    logger.exception(e)
                    artist = ''

                try:
                    album = tags['album'][0]
                except (KeyError, IndexError) as e:
                    logger.exception(e)
                    album = ''

                try:
                    track = int(re.match(r"(\d+)(?:/\d+)?", tags['tracknumber'][0])[1])
                except (KeyError, IndexError) as e:
                    logger.exception(e)
                    track = 0

                title = tags['title'][0]
                logger.debug(f"Loaded FLAC/Opus file: '{artist} / {album} / {track} - {title}'")
                return artist, album, track, title

            case mutagen.mp4.MP4:
                try:
                    artist = tags['\xa9ART'][0]
                except (KeyError, IndexError) as e:
                    logger.exception(e)
                    artist = ''

                try:
                    album = tags['\xa9alb'][0]
                except (KeyError, IndexError) as e:
                    logger.exception(e)
                    album = ''

                try:
                    track = int(tags['trkn'][0][0])
                except (KeyError, IndexError) as e:
                    logger.exception(e)
                    track = ''

                title = tags['\xa9nam'][0]
                logger.debug(f"Loaded MP4/M4A file: '{artist} / {album} / {track} - {title}'")
                return artist, album, track, title
            case _:
                logger.info(f"Found unsupported file: '{tags}'")

    except mutagen.MutagenError as e:
        logger.exception(e)

    return None


def make_search_url(artist: str, title: str) -> str:
    """
    Combine artist and title information to generate the search URL

    :param artist: Artist name
    :param title: Title
    :return: The full search URL
    """
    search_url = BASE_URL + "/search?q=" + urllib.parse.quote_plus(artist + " " + title)
    logger.debug(f"Generated search URL '{search_url}'")
    return search_url


async def download_url(session: aiohttp.ClientSession, url: str) -> BeautifulSoup:
    """
    Download and parse the provided URL

    :param session: An open HTTP session to execute the request
    :param url: The requested URL
    :return: A BeautifulSoup object initialized with the contents of the downloaded page
    """
    logger.debug(f"Downloading HTML at '{url}'")

    async with session.get(url) as response:
        html_content = await response.text('latin-1')
        return BeautifulSoup(html_content, "html.parser")


async def download_file(session: aiohttp.ClientSession, url: str, destination: Path) -> None:
    """
    Download the provided URL as a file

    :param session: An open HTTP session to execute the request
    :param url: The requested URL
    :param destination: The output file destination
    """
    logger.debug(f"Downloading file at '{url}' in '{destination}'")

    async with session.get(url) as response:
        file_content = await response.read()

    async with aiofiles.open(destination, mode='wb') as f:
        await f.write(file_content)


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
    asyncio.run(download_all_lyrics())

    # Stop profiler and print stats
    pr.disable()
    ps = pstats.Stats(pr)
    ps.sort_stats('cumulative').print_stats()
