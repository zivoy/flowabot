"""
all functions and utilities related to osu
"""
# TODO: break up into separate files and make it into a proper module
import io
import math
import os
import warnings
import zipfile
from enum import Enum
from textwrap import wrap
from time import strftime, gmtime, time
from typing import Union, Tuple, List

import arrow
import bezier
import discord
import matplotlib
import numpy as np
import pandas as pd
import regex
import requests
import seaborn as sns
from PIL import Image
# from arrow.factory import ArrowParseWarning
from matplotlib import pyplot as plt
import oppadc as oppa

from utils import dict_string_to_nums, fetch_emote, Log, Config, DATE_FORM, \
    SEPARATOR, UserNonexistent, Dict, format_nums, UserError, Api

# warnings.simplefilter("ignore", ArrowParseWarning)

OSU_API = Api("https://osu.ppy.sh/api", 60, {"k": Config.credentials.osu_api_key})


class MapError(Exception):
    """
    general errors related to maps
    """


class BadLink(MapError):
    """
    error in the case that the map link was invalid
    """


class BadMapFile(MapError):
    """
    error in the case that the map file was invalid
    """


class BadId(MapError):
    """
    error in the case that the map id was invalid
    """


class NoPlays(UserError):
    """
    error in the case that the user has no plays on map
    """


class BadMapObject(MapError):
    """
    error in the case that the map object is invalid
    """


class NoLeaderBoard(MapError):
    """
    error in the case that the there was no leader board found for map
    """


class NoBeatmap(MapError):
    """
    error in the case that there was no beatmap
    """


class NoScore(UserError):
    """
    error in the case that the user does not have a score on map
    """


class NoReplay(UserError):
    """
    error in the case that the user has no replay on the map
    """


class OsuConsts(Enum):
    """
    all constants related to osu
    """
    # "": 0,
    MODS = {
        "NF": 1 << 0,
        "EZ": 1 << 1,
        "TD": 1 << 2,
        "HD": 1 << 3,
        "HR": 1 << 4,
        "SD": 1 << 5,
        "DT": 1 << 6,
        "RX": 1 << 7,
        "HT": 1 << 8,
        "NC": 1 << 9,
        "FL": 1 << 10,
        "AT": 1 << 11,
        "SO": 1 << 12,
        "AP": 1 << 13,
        "PF": 1 << 14,
        "4K": 1 << 15,
        "5K": 1 << 16,
        "6K": 1 << 17,
        "7K": 1 << 18,
        "8K": 1 << 19,
        "FI": 1 << 20,
        "RD": 1 << 21,
        "LM": 1 << 22,
        "TR": 1 << 23,
        "9K": 1 << 24,
        "10K": 1 << 25,
        "1K": 1 << 26,
        "3K": 1 << 27,
        "2K": 1 << 28,
        "V2": 1 << 29
    }

    MODS_INT = {v: k for k, v in MODS.items()}

    DIFF_MODS = ["HR", "EZ", "DT", "HT", "NC", "FL", "HD", "NF"]
    TIME_MODS = ["DT", "HT", "NC"]

    AR_MS_STEP1 = 120
    AR_MS_STEP2 = 150
    AR0_MS = 1800
    AR5_MS = 1200
    AR10_MS = 450
    OD_MS_STEP = 6
    OD0_MS = 79.5
    OD10_MS = 19.5

    DT_SPD = 1.5
    HT_SPD = .75

    HR_AR = 1.4
    EZ_AR = 0.5

    HR_CS = 1.3
    EZ_CS = 0.5

    HR_OD = 1.4
    EZ_OD = 0.5

    HR_HP = 1.4
    EZ_HP = 0.5

    STRAIN_STEP = 400.0
    DECAY_BASE = [0.3, 0.15]
    STAR_SCALING_FACTOR = 0.0675
    EXTREME_SCALING_FACTOR = 0.5
    DECAY_WEIGHT = 0.9


MODS_RE = regex.compile(rf"^({'|'.join(OsuConsts.MODS.value.keys())})+$")


def calculate_acc(n300: int, n100: int, n50: int, nMiss: int) -> float:
    """
    calculate the acc based on number of hits

    :param n300: number of 300s
    :param n100: number of 100s
    :param n50: number of 50s
    :param nMiss: number of misses
    :return: accuracy
    """
    return (50 * n50 + 100 * n100 + 300 * n300) / (300 * (nMiss + n50 + n100 + n300))


class Play:
    def __init__(self, play_dict: dict):
        """
        organises the dict response from osu api into object

        :param play_dict: dict from api
        """
        dict_string_to_nums(play_dict)

        self.score = play_dict["score"]
        self.maxcombo = play_dict["maxcombo"]
        self.countmiss = play_dict["countmiss"]
        self.count50 = play_dict["count50"]
        self.count100 = play_dict["count100"]  # + play_dict["countkatu"]
        self.count300 = play_dict["count300"]  # + play_dict["countgeki"]
        self.perfect = play_dict["perfect"]
        self.enabled_mods = parse_mods_int(play_dict["enabled_mods"])
        self.user_id = play_dict["user_id"]
        self.date = arrow.get(play_dict["date"], DATE_FORM)
        self.rank = play_dict["rank"]
        self.accuracy = calculate_acc(self.count300, self.count100, self.count50, self.countmiss)

        if "beatmap_id" in play_dict:
            self.beatmap_id = play_dict["beatmap_id"]
        else:
            self.beatmap_id = 0

        if "replay_available" in play_dict:
            self.replay_available = play_dict["replay_available"]
        else:
            self.replay_available = 0

        if "score_id" in play_dict:
            self.score_id = play_dict["score_id"]
        else:
            self.score_id = ""

        if "pp" in play_dict:
            self.performance_points = play_dict["pp"]
        else:
            self.performance_points = None

    def __eq__(self, other):
        return self.date == other.date and self.user_id == other.user_id


class CalculateMods:
    def __init__(self, mods: Union[list, str]):
        """
        calculates the modifications that happens to values when you apply mods

        :param mods: mods to apply
        """
        self.mods = mods
        if list(mods) != mods:
            self.mods: list = parse_mods_string(mods)

        #     Log.log(mods.replace("+", ""))
        # Log.log(self.mods)

    def ar(self, raw_ar: Union[float, int]) -> Tuple[float, float, list]:
        """
        calculates approach rate with mods allied to it

        :param raw_ar: input ar
        :return: outputs new ar and how long you have to react as well as mods applied
        """
        ar_multiplier = 1.

        speed = speed_multiplier(self.mods)

        if "HR" in self.mods:
            ar_multiplier *= OsuConsts.HR_AR.value
        elif "EZ" in self.mods:
            ar_multiplier *= OsuConsts.EZ_AR.value

        ar = raw_ar * ar_multiplier

        if ar <= 5:
            ar_ms = OsuConsts.AR0_MS.value - OsuConsts.AR_MS_STEP1.value * ar
        else:
            ar_ms = OsuConsts.AR5_MS.value - OsuConsts.AR_MS_STEP2.value * (ar - 5)

        if ar_ms < OsuConsts.AR10_MS.value:
            ar_ms = OsuConsts.AR10_MS.value
        if ar_ms > OsuConsts.AR0_MS.value:
            ar_ms = OsuConsts.AR0_MS.value

        ar_ms /= speed

        if ar <= 5:
            ar = (OsuConsts.AR0_MS.value - ar_ms) / OsuConsts.AR_MS_STEP1.value
        else:
            ar = 5 + (OsuConsts.AR5_MS.value - ar_ms) / OsuConsts.AR_MS_STEP2.value

        return ar, ar_ms, self.mods

    def cs(self, raw_cs: Union[float, int]) -> Tuple[float, list]:
        """
        calculate the circle size with mod applied to it

        :param raw_cs: input cs
        :return: outputs new cs and mods applied to it
        """
        cs_multiplier = 1.

        if "HR" in self.mods:
            cs_multiplier *= OsuConsts.HR_CS.value
        elif "EZ" in self.mods:
            cs_multiplier *= OsuConsts.EZ_CS.value

        cs = min(raw_cs * cs_multiplier, 10)

        return cs, self.mods

    def od(self, raw_od: Union[float, int]) -> Tuple[float, float, list]:
        """
        calculates the overall difficulty with mods allied to it

        :param raw_od: input od
        :return: new od, how long you have to react in ms and mod allied
        """
        od_multiplier = 1.
        speed = 1.

        if "HR" in self.mods:
            od_multiplier *= OsuConsts.HR_OD.value
        elif "EZ" in self.mods:
            od_multiplier *= OsuConsts.EZ_OD.value

        if "DT" in self.mods:
            speed *= OsuConsts.DT_SPD.value
        elif "HT" in self.mods:
            speed *= OsuConsts.HT_SPD.value

        od = raw_od * od_multiplier

        odms = OsuConsts.OD0_MS.value - math.ceil(OsuConsts.OD_MS_STEP.value * od)
        odms = min(max(OsuConsts.OD10_MS.value, odms), OsuConsts.OD0_MS.value)

        odms /= speed

        od = (OsuConsts.OD0_MS.value - odms) / OsuConsts.OD_MS_STEP.value

        return od, odms, self.mods

    def hp(self, raw_hp: Union[float, int]) -> Tuple[float, list]:
        """
        calculates the hp with the mods applied

        :param raw_hp: input hp
        :return: outputs hp and mods applied to it
        """
        hp_multiplier = 1.

        if "HR" in self.mods:
            hp_multiplier *= OsuConsts.HR_HP.value
        elif "EZ" in self.mods:
            hp_multiplier *= OsuConsts.EZ_HP.value

        hp = min(raw_hp * hp_multiplier, 10)

        return hp, self.mods


def parse_mods_string(mods: str) -> list:
    """
    turns mod str into mod list

    :param mods: mod string
    :return: mod list
    """
    if mods == '' or mods == "nomod":
        return []
    mods = mods.replace("+", "").upper()
    mods_included = MODS_RE.match(mods)
    if mods_included is None:
        Log.error(f"Mods not valid: {mods}")
        return []  # None
    matches = mods_included.captures(1)
    return list(set(matches))


def parse_mods_int(mods: int) -> list:
    """
    turns bitwise flag into mod list

    :param mods: mod int
    :return: mod list
    """
    if not mods:
        return []
    mod_list = list()
    for i in OsuConsts.MODS_INT.value:
        if i & mods:
            mod_list.append(OsuConsts.MODS_INT.value[i])
    return mod_list


def sanitize_mods(mods: Union[list, set]) -> Union[list, set]:
    """
    gets rid of mods that have similar effects

    :param mods: mod list
    :return: fixed mod list
    """
    if "NC" in mods and "DT" in mods:
        mods.remove("DT")
    if "PF" in mods and "SD" in mods:
        mods.remove("SD")
    return mods


def speed_multiplier(mods: Union[list, set]) -> float:
    """
    gets the speed multiplier based on mods applied

    :param mods: list of mods
    :return: speed multiplier
    """
    speed = 1.
    if "DT" in mods or "NC" in mods:
        speed *= OsuConsts.DT_SPD.value
    elif "HT" in mods:
        speed *= OsuConsts.HT_SPD.value
    return speed


def mod_int(mod_list: Union[list, set, int]) -> int:
    """
    cleans and turns the list of mods into bitwise integer

    :param mod_list: list of mods
    :return: bitwise flag
    """
    if isinstance(mod_list, int):
        return mod_list
    elif isinstance(mod_list, str):
        mod_list = parse_mods_string(mod_list)
    else:
        mod_list = set(mod_list)
    if "NC" in mod_list:
        mod_list.add("DT")

    mod_list = filter(lambda x: x in OsuConsts.DIFF_MODS.value, mod_list)

    res = 0

    for i in mod_list:
        res += OsuConsts.MODS.value[i]
    return res


def get_user(user: Union[int, str]) -> dict:
    """
    gets users profile information

    :param user: username
    :return: dictionary containing the information
    """
    response = OSU_API.get('/get_user', {"u": user})
    response = response.json()

    if Config.debug:
        Log.log(response)

    if not response:
        raise UserNonexistent(f"Couldn't find user: {user}")

    for i, _ in enumerate(response):
        response[i]["join_date"] = arrow.get(response[i]["join_date"], DATE_FORM)
        dict_string_to_nums(response[i])

    return response


def get_leaderboard(beatmap_id: Union[str, int], limit: int = 100) -> List[Play]:
    """
    gets leader board for beatmap

    :param beatmap_id: beatmap id
    :param limit: number of items to get
    :return: list of plays
    """
    response = OSU_API.get('/get_scores', {"b": beatmap_id, "limit": limit}).json()

    if Config.debug:
        Log.log(response)

    if not response:
        raise NoLeaderBoard("Couldn't find leader board for this beatmap")

    for i, _ in enumerate(response):
        response[i]["beatmap_id"] = beatmap_id
        response[i] = Play(response[i])

    return response


def get_user_map_best(beatmap_id: Union[int, str], user: Union[int, str],
                      enabled_mods: int = 0) -> List[Play]:
    """
    gets users best play on map

    :param beatmap_id: beatmap id
    :param user: username
    :param enabled_mods: mods used
    :return: list of plays
    """
    response = OSU_API.get('/get_scores', {"b": beatmap_id, "u": user, "mods": enabled_mods}).json()

    if Config.debug:
        Log.log(response)

    # if len(response) == 0:
    #     raise NoScore("Couldn't find user score for this beatmap")

    for i, j in enumerate(response):
        response[i] = Play(j)
        response[i].beatmap_id = beatmap_id

    return response


def get_user_best(user: Union[int, str], limit: int = 100) -> List[Play]:
    """
    gets users best plays

    :param user: username
    :param limit: number of items to fetch
    :return: list of plays
    """
    response = OSU_API.get('/get_user_best', {"u": user, "limit": limit})
    response = response.json()

    if Config.debug:
        Log.log(response)

    if not response:
        raise NoPlays(f"No top plays found for {user}")

    for i, j in enumerate(response):
        response[i] = Play(j)

    return response


def get_user_recent(user: Union[int, str], limit: int = 10) -> List[Play]:
    """
    gets user most recent play by index

    :param user: user name
    :param limit: number of items to fetch
    :return: list of plays
    """
    response = OSU_API.get('/get_user_recent', {"u": user, "limit": limit}).json()

    if Config.debug:
        Log.log(response)

    if not response:
        raise NoPlays(f"No recent plays found for {user}")

    for i, j in enumerate(response):
        response[i] = Play(j)

    return response


def get_replay(beatmap_id: Union[int, str], user_id: Union[int, str],
               mods: int, mode: int = 0) -> str:
    """
    gets the replay string of play

    :param beatmap_id: beatmap id
    :param user_id: username
    :param mods: mods used on play
    :param mode: mode played on
    :return: base64 encoded replay string
    """
    response = OSU_API.get("/get_replay", {"b": beatmap_id, "u": user_id,
                                           "mods": mods, "m": mode}).json()

    if "error" in response:
        raise NoReplay("Could not find replay for this user")

    replay = response["content"]

    return replay


def get_rank_emoji(rank: str, client: discord.Client) -> Union[bool, discord.Emoji, str]:
    """
    gets the rank emoji

    :param rank: rank to fetch
    :param client: discord client
    :return: emoji or name
    """
    if rank == "XH":
        emote = fetch_emote("XH_Rank", None, client)
        return emote if emote else "Silver SS"
    if rank == "X":
        emote = fetch_emote("X_Rank", None, client)
        return emote if emote else "SS"
    if rank == "SH":
        emote = fetch_emote("SH_Rank", None, client)
        return emote if emote else "Silver S"
    if rank == "S":
        emote = fetch_emote("S_Rank", None, client)
        return emote if emote else "S"
    if rank == "A":
        emote = fetch_emote("A_Rank", None, client)
        return emote if emote else "A"
    if rank == "B":
        emote = fetch_emote("B_Rank", None, client)
        return emote if emote else "B"
    if rank == "C":
        emote = fetch_emote("C_Rank", None, client)
        return emote if emote else "C"
    if rank == "D":
        emote = fetch_emote("D_Rank", None, client)
        return emote if emote else "D"
    if rank == "F":
        emote = fetch_emote("F_Rank", None, client)
        return emote if emote else "Fail"
    return False


def get_top(user: str, index: int, rb: bool = False, ob: bool = False) -> Play:
    """
    gets user top play

    :param user: username
    :param index: index to get
    :param rb: sort by recent best
    :param ob: sort by old best
    :return: Play object
    """
    index = min(max(index, 1), 100)
    limit = 100 if rb or ob else index
    response = get_user_best(user, limit)

    if rb:
        response = sorted(response, key=lambda k: k.date, reverse=True)
    if ob:
        response = sorted(response, key=lambda k: k.date)

    if len(response) < index:
        index = len(response)

    recent_raw = response[index - 1]

    return recent_raw


def get_recent(user: str, index: int) -> Play:
    """
    gets the users recent play by index

    :param user: username of player
    :param index: index to fetch
    :return: Play object
    """
    index = min(max(index, 1), 50)
    response = get_user_recent(user, index)

    if len(response) < index:
        index = len(response)

    recent_raw = response[index - 1]

    return recent_raw


class MapStats:
    def __init__(self, map_id: Union[str, int], mods: list, link_type: str = "id"):
        """
        get stats on map // map api

        :param map_id:
        :param mods: mod
        :param link_type: [id|map|path|url]
        :return: map data dict, map object with calculated values
        """
        if link_type == "id":
            link = f"https://osu.ppy.sh/osu/{map_id}"
        else:
            link = map_id

        if link_type == "map":
            raw_map = link
        elif link_type == "path":
            with open(link, "r") as mp:
                raw_map = mp.read()
        else:
            raw_map = requests.get(link).text
            if raw_map == "":
                raise BadLink

        bmp = oppa.OsuMap(raw_str=raw_map)  # , auto_parse=True)

        if not bmp.hitobjects:
            raise BadMapFile

        speed = speed_multiplier(mods)

        map_creator = get_user(bmp.creator)[0]
        stats = bmp.getStats(mod_int(mods))
        diff = CalculateMods(mods)

        length = bmp.hitobjects[-1].starttime
        change_list = [i for i in bmp.timingpoints if i.change]
        bpm_avg = list()
        bpm_list = list()
        for j, i in enumerate(change_list):
            if i.change:
                if j + 1 == len(change_list):
                    dur = length - i.starttime
                else:
                    dur = change_list[j + 1].starttime - i.starttime
                bpm_avg.append((1000 / i.ms_per_beat * 60) * dur)
                bpm_list.append((1000 / i.ms_per_beat * 60))

        self.speed_multiplier = speed
        self.artist = bmp.artist
        self.title = bmp.title
        self.artist_unicode = bmp.artist_unicode
        self.title_unicode = bmp.title_unicode
        self.version = bmp.version
        self.bpm_min = min(bpm_list) * speed
        self.bpm_max = max(bpm_list) * speed
        self.total_length = (length - bmp.hitobjects[0].starttime) / 1000 / speed
        self.max_combo = None
        self.creator = bmp.creator
        self.creator_id = map_creator["user_id"]
        self.map_creator = Dict(map_creator)
        self.base_cs = bmp.cs
        self.base_ar = bmp.ar
        self.base_od = bmp.od
        self.base_hp = bmp.hp
        self.cs = diff.cs(bmp.cs)[0]
        self.ar = diff.ar(bmp.ar)[0]
        self.od = diff.od(bmp.od)[0]
        self.hp = diff.hp(bmp.hp)[0]
        self.mode = bmp.mode

        self.hit_objects = len(bmp.hitobjects)
        self.count_normal = bmp.amount_circle
        self.count_slider = bmp.amount_slider
        self.count_spinner = bmp.amount_spinner
        bmp.getStats()

        if link_type == "id":
            self.approved = 0
            self.submit_date = arrow
            self.approved_date = arrow
            self.last_update = arrow
            self.beatmap_id = 0
            self.beatmapset_id = 0
            self.source = ""
            self.genre_id = 0
            self.language_id = 0
            self.file_md5 = ""
            self.tags = ""
            self.favourite_count = 0
            self.rating = 0.0
            self.playcount = 0
            self.passcount = 0
            self.download_unavailable = 0
            self.audio_unavailable = 0

            mods_applied = mod_int(mods)
            map_web = OSU_API.get("/get_beatmaps", {"b": map_id, "mods": mods_applied}).json()
            if not map_web:
                raise BadId
            dict_string_to_nums(map_web[0])
            for i, j in map_web[0].items():
                setattr(self, i, j)
            self.submit_date = arrow.get(self.submit_date, DATE_FORM)
            self.approved_date = arrow.get(self.approved_date, DATE_FORM)
            self.last_update = arrow.get(self.last_update, DATE_FORM)

        try:
            self.leaderboard = get_leaderboard(map_id)
        except NoLeaderBoard:
            self.leaderboard = []

        if self.max_combo is None:
            self.max_combo = bmp.maxCombo()
        self.aim_stars = stats.aim  # not sure if its aim or aim_diffeculty
        self.speed_stars = stats.speed
        self.total = stats.total
        self.bpm = sum(bpm_avg) / (length - bmp.hitobjects[0].starttime) * speed
        self.beatmap = bmp


def graph_bpm(map_link: Union[str, int], mods: list, link_type: str):
    """
    graphs the bpm changes on map

    :param map_link: map link or id
    :param mods: mods applied
    :param link_type: is ita link or id
    :return:
    """
    map_obj = MapStats(map_link, mods, link_type)

    Log.log(f"Graphing BPM for {map_obj.title}")

    data = [(i.starttime / map_obj.speed_multiplier,
             1000 / i.ms_per_beat * 60 / map_obj.speed_multiplier)
            for i in map_obj.beatmap.timingpoints if i.change]

    chart_points = list()
    for i, j in enumerate(data):
        if i != 0:
            last = data[i - 1]
            chart_points.append((j[0] - .01, last[1]))
        chart_points.append(j)
        if len(data) - 1 == i:
            chart_points.append((map_obj.beatmap.hitobjects[-1].starttime
                                 / map_obj.speed_multiplier, j[1]))

    points = pd.DataFrame(chart_points)
    points.columns = ["Time", "BPM"]

    col = (38 / 255, 50 / 255, 59 / 255, .9)
    sns.set(rc={'axes.facecolor': col,
                'text.color': (236 / 255, 239 / 255, 241 / 255),
                'figure.facecolor': col,
                'savefig.facecolor': col,
                'xtick.color': (176 / 255, 190 / 255, 197 / 255),
                'ytick.color': (176 / 255, 190 / 255, 197 / 255),
                'grid.color': (69 / 255, 90 / 255, 100 / 255),
                'axes.labelcolor': (240 / 255, 98 / 255, 150 / 255),
                'xtick.bottom': True,
                'xtick.direction': 'in',
                'figure.figsize': (6, 4),
                'savefig.dpi': 100
                })

    ax = sns.lineplot(x="Time", y="BPM", data=points, color=(240 / 255, 98 / 255, 150 / 255))

    length = int(map_obj.total_length) * 1000
    m = length / 50
    plt.xlim(-m, length + m)

    formatter = matplotlib.ticker.FuncFormatter(lambda ms, x: strftime('%M:%S', gmtime(ms // 1000)))
    ax.xaxis.set_major_formatter(formatter)

    comp = round(max(1, (map_obj.bpm_max - map_obj.bpm_min) / 20), 2)
    top = round(map_obj.bpm_max, 2) + comp
    bot = max(round(map_obj.bpm_min, 2) - comp, 0)
    dist = top - bot

    plt.yticks(np.arange(bot, top, dist / 6 - .0001))

    plt.ylim(bot, top)

    round_num = 0 if dist > 10 else 2

    formatter = matplotlib.ticker.FuncFormatter(lambda dig, y:
                                                f"{max(dig - .004, 0.0):.{round_num}f}")
    ax.yaxis.set_major_formatter(formatter)

    ax.xaxis.grid(False)
    width = 85
    map_text = "\n".join(wrap(f"{map_obj.title} by {map_obj.artist}", width=width)) + "\n" + \
               "\n".join(wrap(f"Mapset by {map_obj.creator}, "
                              f"Difficulty: {map_obj.version}", width=width))
    plt.title(map_text)

    plt.box(False)

    image = io.BytesIO()
    plt.savefig(image, bbox_inches='tight')
    image.seek(0)

    plt.clf()
    plt.close()
    return image


def get_map_link(link: str, **kwargs) -> Tuple[Union[int, str], str]:
    """
    gets link type and corresponding value

    :param link: a link or id
    :param kwargs: for the case that its a osz file
    :return: id or other identifier and str of type
    """
    if link.isnumeric():
        return int(link), "id"
    if link.endswith(".osu"):
        return link, "url"
    if "osu.ppy.sh" in link:
        if "#osu/" in link:
            return int(link.split("#osu/")[-1]), "id"
        if "/b/" in link:
            return int(link.split("/b/")[-1]), "id"
        if "/osu/" in link:
            return int(link.split("/osu/")[-1]), "id"
        if "/beatmaps/" in link:
            return int(link.split("/beatmaps/")[-1]), "id"
        if "/discussion/" in link:
            return int(link.split("/discussion/")[-1].split("/")[0]), "id"
    if link.endswith(".osz"):
        return download_mapset(link, **kwargs), "path"


def download_mapset(link_id: Union[str, int] = None, link: str = None) -> str:
    """
    downloads an osu mapset

    :param link_id: a map id
    :param link: a direct link to osz file
    :return: the path to downloaded folder
    """
    if link_id is None and link is None:
        raise NoBeatmap("No beatmap provided")
    if link_id is not None:
        link = f"https://bloodcat.com/osu/s/{link_id}"
        name = str(link_id)
    else:
        name = link.split('/')[-1].split(".osz")[0]

    mapset = requests.get(link)
    headers = mapset.headers.get('content-type').lower()

    if link_id is not None and "octet-stream" not in headers:
        link = f"https://osu.gatari.pw/d/{link_id}"
        mapset = requests.get(link)
        headers = mapset.headers.get('content-type').lower()
        if "octet-stream" not in headers:
            Log.error("Could not find beatmap:", link_id)
            raise NoBeatmap("Could not find beatmap")

    location = os.path.join(Config.osu_cache_path, name)

    map_files = zipfile.ZipFile(io.BytesIO(mapset.content), "r")

    osu_file = regex.compile(r".+\[(\D+)\]\.osu")

    for i in map_files.infolist():
        for j in [".osu", ".jpg", ".jpeg", ".png", ".mp3"]:
            if i.filename.endswith(j):
                if j == ".osu":
                    diff_name = osu_file.match(i.filename).captures(1)[0]
                    i.filename = f"{diff_name}.osu"
                map_files.extract(i, location)
                break

    return location


def stat_play(play: Play):
    """
    gets statistics on osu play and graph on play

    :param play: a users play
    :return: a dict with information on play keys -> [user_id,
                                                        beatmap_id,
                                                        rank,
                                                        score,
                                                        combo,
                                                        count300,
                                                        count100,
                                                        count50,
                                                        countmiss,
                                                        mods,
                                                        date,
                                                        unsubmitted,
                                                        performance_points,
                                                        pb,
                                                        lb,
                                                        username,
                                                        user_rank,
                                                        user_pp,
                                                        stars,
                                                        pp_fc,
                                                        acc,
                                                        acc_fc,
                                                        replay,
                                                        completion,
                                                        strain_bar,
                                                        map_obj,
                                                        {score_id,
                                                        ur}]
    """
    map_obj = MapStats(play.beatmap_id, play.enabled_mods, "id")
    if play.rank.upper() == "F":
        completion = (play.count300 + play.count100 + play.count50 + play.countmiss) \
                     / map_obj.hit_objects
    else:
        completion = 1

    strain_bar = map_strain_graph(map_obj.beatmap, play.enabled_mods, completion)
    try:
        user_leaderboard = get_user_best(play.user_id)
        map_leaderboard = map_obj.leaderboard
        best_score = get_user_map_best(play.beatmap_id, play.user_id, mod_int(play.enabled_mods))
        user = get_user(play.user_id)[0]
    except Exception as err:
        Log.error(err)
        return

    if best_score:
        best_score = best_score[0]

    recent = Dict({
        "user_id": play.user_id,
        "beatmap_id": play.beatmap_id,
        "rank": play.rank,
        "score": play.score,
        "combo": play.maxcombo,
        "count300": play.count300,
        "count100": play.count100,
        "count50": play.count50,
        "countmiss": play.countmiss,
        "mods": play.enabled_mods,
        "date": play.date,
        "unsubmitted": False,
        "performance_points": play.performance_points
    })

    recent.pb = 0
    recent.lb = 0
    replay = 0

    for j, i in enumerate(user_leaderboard):
        if i == play:
            recent.pb = j + 1
            break
    for j, i in enumerate(map_leaderboard):
        if i == play:
            recent.lb = j + 1
            break

    recent.username = user["username"]
    recent.user_rank = user["pp_rank"]
    recent.user_pp = user["pp_raw"]

    if best_score:
        if play == best_score:
            replay = best_score.replay_available
            recent.score_id = best_score.score_id
        else:
            recent.unsubmitted = True

    pp = map_obj.beatmap.getPP(Mods=mod_int(play.enabled_mods), recalculate=True,
                               combo=play.maxcombo, misses=play.countmiss,
                               n300=play.count300, n100=play.count100, n50=play.count50)
    pp_fc = map_obj.beatmap.getPP(Mods=mod_int(play.enabled_mods), n100=play.count100,
                                  n50=play.count50, n300=play.count300 + play.countmiss,
                                  recalculate=True)

    recent.stars = map_obj.total
    recent.pp_fc = pp_fc.total_pp
    recent.acc = pp.accuracy
    recent.acc_fc = pp_fc.accuracy

    if recent.performance_points is None:
        recent.performance_points = pp.total_pp

    recent.replay = None
    if replay:
        recent.replay = get_replay(play.beatmap_id, play.user_id, mod_int(play.enabled_mods), 0)

        recent.ur = 0
        # TODO: make osr parser for unstable rate

    recent.completion = completion
    recent.strain_bar = strain_bar
    recent.map_obj = map_obj

    return recent


def map_strain_graph(map_obj: oppa.OsuMap, mods: Union[list, set], progress: float = 1.,
                     mode: str = "", width: float = 399., height: float = 40.,
                     max_chunks: Union[int, float] = 100, low_cut: float = 30.):
    """
    generats a strains graph based on map

    :param map_obj: map object
    :param mods: mods applied
    :param progress: how much of the map player finished
    :param mode: [aim|speed] for type of strains to get
    :param width: width of image
    :param height: height of image
    :param max_chunks: resolution to get out of map
    :param low_cut: adds some beefing to the bottem
    :return: an image in a bytesio object
    """
    map_strains = get_strains(map_obj, mods, mode)
    strains, max_strain = map_strains["strains"], map_strains["max_strain"]

    strains_chunks = list()
    chunk_size = math.ceil(len(strains) / max_chunks)

    for i in range(0, len(strains), chunk_size):
        strain_part = strains[i:i + chunk_size]
        strains_chunks.append(max(strain_part))

    x = np.linspace(0, width, num=len(strains_chunks))
    y = np.minimum(low_cut,
                   height * 0.125 + height * .875 - np.array([i / max_strain for i in
                                                              strains_chunks]) * height * .875)

    x = np.insert(x, 0, 0)
    x = np.insert(x, 0, 0)
    x = np.append(x, width)
    x = np.append(x, width)
    y = np.insert(y, 0, low_cut)
    y = np.insert(y, 0, low_cut)
    y = np.append(y, low_cut)
    y = np.append(y, low_cut)
    curves = list()
    curves.append(bezier.Curve(np.asfortranarray([[0.0, 0.0], [height, low_cut]]), degree=1))
    for i in range(1, len(y) - 1):
        node = np.asfortranarray([
            [avgpt(x, i - 1), x[i], avgpt(x, i)],
            [avgpt(y, i - 1), y[i], avgpt(y, i)]])
        curves.append(
            bezier.Curve(node, degree=2)
        )
    curves.append(bezier.Curve(np.asfortranarray([[width, width], [low_cut, height]]), degree=1))
    curves.append(bezier.Curve(np.asfortranarray([[width, 0.0], [height, height]]), degree=1))
    polygon = bezier.CurvedPolygon(*curves)

    _, ax = plt.subplots(figsize=(round(width * 1.30), round(height * 1.30)), dpi=1)
    polygon.plot(pts_per_edge=200, color=(240 / 255, 98 / 255, 146 / 255, 1), ax=ax)
    plt.xlim(0, width)
    plt.ylim(height, 0)
    plt.axis('off')
    plt.box(False)

    image = io.BytesIO()
    fig1 = plt.gcf()
    fig1.savefig(image, bbox_inches='tight', transparent=True, pad_inches=0, dpi=1)
    image.seek(0)
    plt.clf()
    plt.close()

    img = Image.open(image)
    data = np.array(img)
    for j in data:
        for pos, i in enumerate(j):
            if pos > len(j) * progress:
                j[pos] = i / 1.5

            if i[3] != 0:
                j[pos][3] = i[3] / 159 * 255

    img = Image.fromarray(data)
    image.close()
    image = io.BytesIO()
    img.save(image, "png")
    image.seek(0)

    return image


def avgpt(points: Union[list, np.array], index: int) -> float:
    """
    get the average between current point and the next one
    :param points: list of points
    :param index: index
    :return: average
    """
    return (points[index] + points[index + 1]) / 2.0


def get_strains(beatmap: oppa.OsuMap, mods: Union[list, set], mode: str = "") -> dict:
    """
    get all stains in map

    :param beatmap: beatmap object
    :param mods: mods used
    :param mode: [aim|speed] for type of strains to get
    :return: dict of strains keys -> [strains, max_strain, max_strain_time,
                                    max_strain_time_real, total]
    """
    stars = beatmap.getStats(mod_int(mods))

    speed = speed_multiplier(mods)

    aim_strains = calculate_strains(1, beatmap.hitobjects, speed)
    speed_strains = calculate_strains(0, beatmap.hitobjects, speed)

    star_strains = list()
    max_strain = 0
    strain_step = OsuConsts.STRAIN_STEP.value * speed
    strain_offset = math.floor(beatmap.hitobjects[0].starttime / strain_step) \
                    * strain_step - strain_step
    max_strain_time = strain_offset

    for i, _ in enumerate(aim_strains):
        star_strains.append(aim_strains[i] + speed_strains[i]
                            + abs(speed_strains[i] - aim_strains[i])
                            * OsuConsts.EXTREME_SCALING_FACTOR.value)

    chosen_strains = star_strains
    total = stars.total
    if mode == "aim":
        total = stars.aim
        chosen_strains = aim_strains
    if mode == "speed":
        total = stars.speed
        chosen_strains = speed_strains

    for i in chosen_strains:
        if i > max_strain:
            max_strain_time = i * OsuConsts.STRAIN_STEP.value + strain_offset
            max_strain = i

    return {
        "strains": chosen_strains,
        "max_strain": max_strain,
        "max_strain_time": max_strain_time,
        "max_strain_time_real": max_strain_time * speed,
        "total": total
    }


def calculate_strains(mode_type: int, hit_objects: list, speed_multiplier: float) -> list:
    """
    get strains of map at all times

    :param mode_type: mode type [speed, aim]
    :param hit_objects: list of hitobjects
    :param speed_multiplier: the speed multiplier induced by mods
    :return: list of strains
    """
    strains = list()
    strain_step = OsuConsts.STRAIN_STEP.value * speed_multiplier
    interval_emd = math.ceil(hit_objects[0].starttime / strain_step) * strain_step
    max_strains = 0.0

    for i, _ in enumerate(hit_objects):
        while hit_objects[i].starttime > interval_emd:
            strains.append(max_strains)
            if i > 0:
                decay = OsuConsts.DECAY_BASE.value[mode_type] ** \
                        (interval_emd - hit_objects[i - 1].starttime) / 1000
                max_strains = hit_objects[i - 1].strains[mode_type] * decay
            else:
                max_strains = 0.0
            interval_emd += strain_step
        max_strains = max(max_strains, hit_objects[i].strains[mode_type])

    strains.append(max_strains)
    for j, i in enumerate(strains):
        i *= 9.999
        strains[j] = math.sqrt(i) * OsuConsts.STAR_SCALING_FACTOR.value

    return strains


def embed_play(play_stats: stat_play, client: discord.Client) -> discord.Embed:
    """
    generates status report embed from play

    :param play_stats: user statistics on play
    :param client:discord client of bot
    :return: discord embed with play stats
    """
    desc = ""
    if play_stats.pb:
        desc = f"**__#{play_stats.pb} Top Play!__**"
    embed = discord.Embed(description=desc,
                          url=f"https://osu.ppy.sh/b/{play_stats.beatmap_id}",
                          title=f"{play_stats.map_obj.artist} – {play_stats.map_obj.title} "
                                f"[{play_stats.map_obj.version}]",
                          color=0xbb5577, inline=False)

    embed.set_author(url=f"https://osu.ppy.sh/u/{play_stats.user_id}",
                     name=f"{play_stats.username} – {play_stats.user_pp:,}pp "
                          f"(#{play_stats.user_rank:,})",
                     icon_url=f"https://a.ppy.sh/{play_stats.user_id}?{int(time())}")

    embed.set_image(url="attachment://strains_bar.png")

    ranked_text = "Submitted"
    approved = play_stats.map_obj.approved
    if approved == 1:
        ranked_text = "Ranked"
    elif approved == 2:
        ranked_text = "Approved"
    elif approved == 3:
        ranked_text = "Qualified"
    elif approved == 4:
        ranked_text = "Loved"

    embed.set_footer(icon_url=f"https://a.ppy.sh/{play_stats.map_obj.creator_id}?{int(time())}",
                     text=f"Mapped by {play_stats.map_obj.creator} {SEPARATOR} {ranked_text} on "
                          f"{play_stats.map_obj.approved_date.format('D MMMM YYYY')}")

    embed.set_thumbnail(url=f"https://b.ppy.sh/thumb/{play_stats.map_obj.beatmapset_id}l.jpg")

    play_results = f"{get_rank_emoji(play_stats.rank, client)} {SEPARATOR} "
    if play_stats.mods:
        play_results += f"+{','.join(sanitize_mods(play_stats.mods))} {SEPARATOR} "

    if play_stats.lb > 0:
        play_results += f"r#{play_stats.lb} {SEPARATOR} "

    play_results += f"{play_stats.score:,} {SEPARATOR} " \
                    f"{format_nums(play_stats.acc, 2)}% {SEPARATOR} " \
                    f"{play_stats.date.humanize()}"

    if play_stats.pp_fc > play_stats.performance_points:
        perfomacne = f"**{'*' if play_stats.unsubmitted else ''}" \
                     f"{format_nums(play_stats.performance_points, 2):,}" \
                     f"pp**{'*' if play_stats.unsubmitted else ''} ➔" \
                     f" {format_nums(play_stats.pp_fc, 2):,}pp for " \
                     f"{format_nums(play_stats.acc_fc, 2)}% FC {SEPARATOR} "
    else:
        perfomacne = f"**{format_nums(play_stats.performance_points, 2):,}pp** {SEPARATOR} "

    if play_stats.combo < play_stats.map_obj.max_combo:
        perfomacne += f"{play_stats.combo:,}/{play_stats.map_obj.max_combo:,}x"
    else:
        perfomacne += f"{play_stats.map_obj.max_combo:,}x"

    if play_stats.pp_fc > play_stats.performance_points:
        perfomacne += "\n"
    elif play_stats.ur or play_stats.count100 or play_stats.count50 or play_stats.countmiss:
        perfomacne += f" {SEPARATOR} "

    if play_stats.count100 > 0:
        perfomacne += f"{play_stats.count100}x100"

    if play_stats.count50 > 0:
        if play_stats.count100 > 0:
            perfomacne += f" {SEPARATOR} "
        perfomacne += f"{play_stats.count50}x50"

    if play_stats.countmiss > 0:
        if play_stats.count100 > 0 or play_stats.count50 > 0:
            perfomacne += f" {SEPARATOR} "
        perfomacne += f"{play_stats.countmiss}xMiss"

    if play_stats.ur is not None and play_stats.ur > 0:
        pass
        # TODO: implrmrnt UR and CV

    if play_stats.completion < 1:
        perfomacne += f"\n**{format_nums(play_stats.completion * 100, 2)}%** completion"

    embed.add_field(name=play_results, value=perfomacne, inline=False)

    beatmap_info = \
        f"{arrow.Arrow(2019, 1, 1).shift(seconds=play_stats.map_obj.total_length).format('mm:ss')}"\
        f" ~ CS**{format_nums(play_stats.map_obj.cs, 1)}** " \
        f"AR**{format_nums(play_stats.map_obj.ar, 1)}** " \
        f"OD**{format_nums(play_stats.map_obj.od, 1)}** " \
        f"HP**{format_nums(play_stats.map_obj.hp, 1)}** ~ "

    if play_stats.map_obj.bpm_min != play_stats.map_obj.bpm_max:
        beatmap_info += f"{format_nums(play_stats.map_obj.bpm_min, 1)}-" \
                        f"{format_nums(play_stats.map_obj.bpm_max, 1)} " \
                        f"(**{format_nums(play_stats.map_obj.bpm, 1)}**) "
    else:
        beatmap_info += f"**{format_nums(play_stats.map_obj.bpm, 1)}** "

    beatmap_info += f"BPM ~ " \
                    f"**{format_nums(play_stats.stars, 2)}**★"

    embed.add_field(name="Beatmap Information", value=beatmap_info, inline=False)

    return embed
