import discord

import osu_utils
from utils import Log, DIGITS, help_me, UserNonexistent, get_user, Users, DiscordInteractive

interact = DiscordInteractive().interact


class Command:
    command = "top"
    description = "Show a specific top play."
    argsRequired = 0
    usage = "[username]"
    examples = [
        {
            "run": "top",
            "result": "Returns your #1 top play."
        },
        {
            "run": "top5 vaxei",
            "result": "Returns Vaxei's #5 top play."
        }]
    synonyms = [r"top\d+", "rb", "recentbest", "ob", "oldbest"]

    async def call(self, package):
        message, args, user_data, client = package["message_obj"], package["args"], \
                                           package["user_obj"], package["client"]

        if len(args) < 2 and user_data["osu_ign"] == "":
            Log.error("No User provided")
            await help_me(message, "ign-set")
            return

        try:
            user = get_user(args, user_data["osu_ign"], "osu")
        except UserNonexistent:
            interact(message.channel.send, "User does not exist")
            return

        index = DIGITS.match(args[0])

        rb = True if args[0] == "rb" or args[0] == "recentbest" else False
        ob = True if args[0] == "ob" or args[0] == "oldbest" else False

        if index is None:
            index = 1
        else:
            index = int(index.captures(1)[0])

        try:
            top_play = osu_utils.get_top(user, index, rb, ob)
        except osu_utils.NoPlays as err:
            interact(message.channel.send, err)
            return

        try:
            play_data = osu_utils.stat_play(top_play)
        except Exception as err:
            interact(message.channel.send, err)
            Log.error(err)
            return

        Users().update_last_message(message.author.id, top_play.beatmap_id, "id",
                                    top_play.enabled_mods, 1, top_play.accuracy, user, play_data.replay)

        embed = osu_utils.embed_play(play_data, client)
        graph = discord.File(play_data.strain_bar, "strains_bar.png")

        interact(message.channel.send, file=graph, embed=embed)
        Log.log(f"Returning top play #{play_data.pb} for {user}")
