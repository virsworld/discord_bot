# imports
import discord
from discord import app_commands
from dotenv import load_dotenv
import os
import yt_dlp
import asyncio
from pathlib import Path
import random
from googleapiclient.discovery import build
from discord.ui import Button, View

# Setup Youtube DL library
ytdl_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # bind to ipv4 since ipv6 addresses cause issues sometimes
}
ytdl = yt_dlp.YoutubeDL(ytdl_options)

#setup FFmpeg
ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', # remedies issue of "error in pull function" caused by expiring stream link
    'options': '-vn',
}

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, original_url,volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.thumbnail = data.get('thumbnail')
        self.uploader = data.get('uploader')
        self.duration = data.get('duration')
        self.upload_date = data.get('upload_date')
        self.original_url = original_url

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if ('entries' in data):
            # playlist functionality
            return [cls(discord.FFmpegPCMAudio(entry['url'] if stream else ytdl.prepare_filename(entry), **ffmpeg_options), data=entry, original_url=url) for entry in data['entries']]
        else:
            filename = data['url'] if stream else ytdl.prepare_filename(data)
            return [cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data, original_url=url)]
    
    @classmethod
    async def from_query(cls, query: str):
        youtube = build('youtube', 'v3', developerKey=random.choice(YT_API_KEYS))
        request = youtube.search().list(
            part='snippet',
            q=query,
            type='video',
            maxResults=5,
            videoCategoryId='10',  # Music category
        )
        response = request.execute()
        if (not response):
            return False
        
        results=[]
        for item in response['items']:
            video_id = item['id']['videoId']
            title = item['snippet']['title']
            url = f"https://www.youtube.com/watch?v={video_id}"
            results.append((title, url))
        return results
    
    @classmethod
    async def from_data(cls, data, *, position=0):
        url = data.get('url')
        ffmpeg_opts = ffmpeg_options.copy()
        if position >= 0:
            ffmpeg_opts['options'] += f" -ss {position}"
        source = discord.FFmpegPCMAudio(url, **ffmpeg_opts)
        original_url = data.get('webpage_url')
        return cls(source, data=data, original_url=original_url)

# custom subclasss of discord client used to implement command tree
class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.current_voice_channel = None
        self.queue = []
        self.is_playing = False
        self.current_song = None
        self.history = []
        self.loop_song = False
        self.loop_queue = False
        self.joined = False

    async def setup_hook(self):
        # This copies the global commands over to your guild.
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)
    
    async def play_next(self):
        if (self.loop_song and self.current_song):
            new_source = await YTDLSource.from_data(self.current_song.data)
            self.current_voice_channel.play(new_source, after=lambda e: self.loop.create_task(self.play_next()))
        elif (self.loop_queue and self.queue):
            self.queue.append(self.current_song)
            self.is_playing = True
            self.current_song = self.queue.pop(0)
            self.history.append(self.current_song)
            new_source = await YTDLSource.from_data(self.current_song.data)
            self.current_voice_channel.play(new_source, after=lambda e: self.loop.create_task(self.play_next()))
        elif (self.queue):
            self.is_playing = True
            self.current_song = self.queue.pop(0) 
            self.history.append(self.current_song)
            self.current_voice_channel.play(self.current_song, after=lambda e: self.loop.create_task(self.play_next()))
            if self.loop_queue:
                self.queue.append(self.current_song)
        else:
            self.is_playing = False

class SongSelectionView(View):
    def __init__(self, results):
        super().__init__(timeout=30.0)  # Set the timeout for how long the view will listen for interactions
        self.results = results
        self.selected_song = None

        for i, (title, url) in enumerate(results):
            # Add a button for each result
            if (len(f"{i + 1}. {title}") > 80):
                title = title[:77]
            button = Button(label=f"{i + 1}. {title}", custom_id=str(i))
            button.callback = self.create_callback(url)
            self.add_item(button)

    def create_callback(self, url):
        async def callback(interaction: discord.Interaction):
            self.selected_song = url
            self.stop()  # Stop listening for interactions
            await interaction.response.send_message(f"Selected: {self.results[int(interaction.data['custom_id'])][0]}")
        return callback

# load sercets
load_dotenv()
MY_GUILD = discord.Object(os.environ['GUILD'])
YT_API_KEYS = [os.environ['YOUTUBE_API_KEY_1'], os.environ['YOUTUBE_API_KEY_2'], os.environ['YOUTUBE_API_KEY_3']]

# start client
intents = discord.Intents.default()
client = MyClient(intents=intents)
discord.opus.load_opus("/opt/local/lib/libopus.dylib")

# commands
@client.tree.command()
@app_commands.describe(
    query = "URL to play"
)
async def play(interaction: discord.Interaction, query: str):
    """plays song"""
    if (not client.joined):
        if(interaction.user.voice):
            client.current_voice_channel = await interaction.user.voice.channel.connect()
            # client.current_text_channel = interaction.channel
            client.joined = True
        else:
            msg = f":no_entry:Please join a voice channel first."
            embed = discord.Embed(description=f"*{msg}*")
            await interaction.response.send_message(embed=embed)
            return
    if (client.current_voice_channel):
        defer = False
        if (not query.startswith("http://") and not query.startswith("https://")):
            await interaction.response.defer(ephemeral=True)
            defer = True
            results = await YTDLSource.from_query(query)
            if not results:  
                msg = f":no_entry:Failed to find a match on Youtube, try a different query."
                embed = discord.Embed(description=f"*{msg}*")
                await interaction.followup.send(embed=embed)
                return
            
            view = SongSelectionView(results)
            await interaction.followup.send(view=view)

            await view.wait()  # Wait until the user makes a selection or the view times out

            if view.selected_song is None:
                await interaction.followup.send(":no_entry: No selection was made.")
                return
            query = view.selected_song

        players = await YTDLSource.from_url(query, stream=True)
        if (not players):
                msg = f":no_entry:Failed to find a match on Youtube, try a different query."
                embed = discord.Embed(description=f"*{msg}*")
                if (defer):
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.response.send_message(embed=embed)
                return
        client.queue.extend(players)
        if (not client.is_playing):
            await client.play_next()
            if (client.queue):
                embed = discord.Embed(title=f"Added to queue", description=f"*{client.queue[-1].title}*", color=discord.Color.blue())
            else:
                embed = discord.Embed(title=f"Added to queue", description=f"*{client.current_song.title}*", color=discord.Color.blue())
            if (client.current_song):
                try:
                    embed.add_field(name="Artist", value=client.current_song.uploader, inline=True)
                except Exception as e:
                    print(f"{e} for artist")
                try:
                    embed.add_field(name="Duration", value=f"{client.current_song.duration // 60}:{client.current_song.duration % 60:02d}", inline=True)
                except Exception as e:
                    print(f"{e} for duration")
                try:
                    embed.add_field(name="Release Date", value=client.current_song.upload_date, inline=True)
                except Exception as e:
                    print(f"{e} for release date")
                try:
                    embed.set_thumbnail(url=client.current_song.thumbnail)
                except Exception as e:
                    print(f"{e} for thumbnail url")
                if (len(query) > 1024):
                    query = query[:1024]
                embed.add_field(name="URL", value=query, inline=False)

                if (defer):
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.response.send_message(embed=embed)
        else:
            embed = discord.Embed(title=f"Added to queue", description=f"*{client.queue[-1].title}*", color=discord.Color.blue())
            try:
                embed.add_field(name="Artist", value=client.queue[-1].uploader, inline=True)
            except Exception as e:
                print(f"{e} for artist")
            try:
                embed.add_field(name="Duration", value=f"{client.queue[-1].duration // 60}:{client.queue[-1].duration % 60:02d}", inline=True)
            except Exception as e:
                print(f"{e} for duration")
            try:
                embed.add_field(name="Release Date", value=client.queue[-1].upload_date, inline=True)
            except Exception as e:
                print(f"{e} for release date")
            try:
                embed.set_thumbnail(url=client.queue[-1].thumbnail)
            except Exception as e:
                print(f"{e} for thumbnail url")
            if (len(query) > 1024):
                query = query[:1024]
            embed.add_field(name="URL", value=query, inline=False)
            if (defer):
                await interaction.followup.send(embed=embed)
            else:
                await interaction.response.send_message(embed=embed)
    else:
        msg = f":no_entry:Please join a voice channel first."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def pause(interaction: discord.Interaction):
    """pauses song"""
    if (client.current_voice_channel and client.current_voice_channel.is_playing()):
        client.current_voice_channel.pause()
        msg = ":pause_button:Paused."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
    else:
        msg = ":no_entry:There is no audio playing."
        embed = discord.Embed(title="", description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def resume(interaction: discord.Interaction):
    """resume song"""
    if (client.current_voice_channel and client.current_voice_channel.is_paused()):
        client.current_voice_channel.resume()
        msg = ":arrow_forward:Resumed."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
    else:
        msg = ":no_entry:The audio is not paused."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
            

@client.tree.command()
async def stop(interaction: discord.Interaction):
    """clears queue and leaves"""
    if (client.current_voice_channel):
        client.queue.clear()
        client.current_voice_channel.stop()
        await client.current_voice_channel.disconnect()
        msg = ":saluting_face:Queue has been cleared and I have disconnected."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
        # resetting client in case user wants to revive bot
        client.current_voice_channel = None
        client.queue = []
        client.current_song = None
        client.history = []
        client.is_playing = False
        client.loop_song = False
        client.loop_queue = False
        client.joined = False
    else:
        msg = ":no_entry:The bot must be active for this command to be called."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def skip(interaction: discord.Interaction):
    """skip current song"""
    if (client.current_voice_channel and client.current_voice_channel.is_playing()):
        client.current_voice_channel.pause()
        await client.play_next()
        if (client.is_playing):
            embed = discord.Embed(title=f"Now Playing", description=f"*{client.current_song.title}*", color=discord.Color.blue())
            query = client.current_song.original_url
            try:
                embed.add_field(name="Artist", value=client.current_song.uploader, inline=True)
            except Exception as e:
                print(f"{e} for artist")
            try:
                embed.add_field(name="Duration", value=f"{client.current_song.duration // 60}:{client.current_song.duration % 60:02d}", inline=True)
            except Exception as e:
                print(f"{e} for duration")
            try:
                embed.add_field(name="Release Date", value=client.current_song.upload_date, inline=True)
            except Exception as e:
                print(f"{e} for release date")
            try:
                embed.set_thumbnail(url=client.current_song.thumbnail)
            except Exception as e:
                print(f"{e} for thumbnail url")
            if (len(query) > 1024):
                query = query[:1024]
            embed.add_field(name="URL", value=query, inline=False)
            await interaction.response.send_message(embed=embed)
        else:
            msg = f":white_check_mark:Skipped, queue complete."
            embed = discord.Embed(description=f"*{msg}*")
            await interaction.response.send_message(embed=embed)
    else:
        msg = ":no_entry:There is no audio playing."
        embed = discord.Embed(title="", description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def queue(interaction: discord.Interaction):
    """displays song queue"""
    if (client.queue):
        embed = discord.Embed(title=f"Queue List", color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.pinimg.com/1200x/a3/50/c9/a350c99315da169bf3ae5f307391b1bf.jpg")
        for i, song in enumerate(client.queue):
            try:
                embed.add_field(name=f"{i+1}. {song.title}", value=f"Artist: {song.uploader}\nDuration: {song.duration // 60}:{song.duration % 60:02d}", inline=False)
            except Exception as e:
                embed.add_field(name=f"{i+1}. {song.title}", value=f"Artist: {song.uploader}", inline=False)
                print(f"{e} for duration")
        await interaction.response.send_message(embed=embed)
    else:
        msg = ":no_entry:The queue is empty."
        embed = discord.Embed(title="", description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def remove(interaction: discord.Interaction, pos: int):
    """removes song from queue"""
    if (0 <= pos <= len(client.queue)):
        removed_song = client.queue.pop(pos-1)
        msg = f":white_check_mark:Removed song: {removed_song.title}"
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
    else:
        msg = f":no_entry:Invalid queue position."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def clear(interaction: discord.Interaction):
    """clears song queue"""
    if (client.queue):
        client.queue.clear()
        msg = f":white_check_mark:The queue has been cleared."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
    else:
        msg = ":no_entry:The queue is empty."
        embed = discord.Embed(title="", description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)


@client.tree.command()
@app_commands.describe(
    volume="Volume level (0 to 100)"
)
async def volume(interaction: discord.Interaction, volume: int):
    """set song volume"""
    if (client.current_voice_channel and client.current_voice_channel.is_playing()):
        player = client.current_voice_channel.source
        if isinstance(player, discord.PCMVolumeTransformer):
            player.volume = volume / 100
            msg = f":white_check_mark:volume set to {volume}%"
            embed = discord.Embed(description=f"*{msg}*")
            await interaction.response.send_message(embed=embed)
        else:
            msg = f":no_entry:Failed to set the volume."
            embed = discord.Embed(description=f"*{msg}*")
            await interaction.response.send_message(embed=embed)
    else:
        msg = ":no_entry:There is no audio playing."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def shuffle(interaction: discord.Interaction):
    """shuffles song queue"""
    if client.queue:
        random.shuffle(client.queue)
        msg = f":white_check_mark:The queue has been shuffled."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
    else:
        msg = f":no_entry:The queue is empty."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
@app_commands.describe(
    mode="Mode to loop (song/queue)"
)
async def loop(interaction: discord.Interaction, mode: str):
    """loops current song or entire queue"""
    if (mode == 'song'):
        client.loop_song = not client.loop_song
        client.loop_queue = False
        status = "enabled" if client.loop_song else "disabled"
        if (status == "enabled"):
            msg = f":white_check_mark:Song loop is {status}."
        else:
            msg = f":no_entry:Song loop is {status}."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
    elif (mode == 'queue'):
        client.loop_queue = not client.loop_queue
        client.loop_song = False
        status = "enabled" if client.loop_queue else "disabled"
        if (status == "enabled"):
            msg = f":white_check_mark:Song loop is {status}."
        else:
            msg = f":no_entry:Queue loop is {status}."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
    else:
        msg = f":no_entry:invalid mode (options: song, queue)"
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
@app_commands.describe(
    position = "Position to seek to"
)
async def seek(interaction: discord.Interaction, position: int):
    """seeks to specific point in song"""
    if (client.current_voice_channel and client.current_voice_channel.is_playing()):
        client.current_voice_channel.pause()
        new_source = await YTDLSource.from_data(client.current_song.data, position=position)
        client.current_voice_channel.play(new_source, after=lambda e: client.loop.create_task(client.play_next()))
        msg = f":white_check_mark:Seeked to {position} seconds in the song."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)
    else:
        msg = f":no_entry:There is no audio playing."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def history(interaction: discord.Interaction):
    if (client.history):
        embed = discord.Embed(title=f"Play History", color=discord.Color.blue())
        embed.set_thumbnail(url="https://cdn.pixabay.com/photo/2023/07/27/04/16/lofi-8152391_1280.png")
        for i, song in enumerate(client.history):
            embed.add_field(name=f"{i+1}. {song.title}", value=f"Artist: {song.uploader}\nDuration: {song.duration // 60}:{song.duration % 60:02d}", inline=False)
        await interaction.response.send_message(embed=embed)
    else:
        msg = f":no_entry:No recent audio has been played."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command(description="shows information about current song")
async def nowplaying(interaction: discord.Interaction):
    if (client.current_song):
        query = client.current_song.original_url
        embed = discord.Embed(title=f"Now Playing", description=f"*{client.current_song.title}*", color=discord.Color.blue())
        try:
            embed.add_field(name="Artist", value=client.current_song.uploader, inline=True)
        except Exception as e:
            print(f"{e} for artist")
        try:
            embed.add_field(name="Duration", value=f"{client.current_song.duration // 60}:{client.current_song.duration % 60:02d}", inline=True)
        except Exception as e:
            print(f"{e} for duration")
        try:
            embed.add_field(name="Release Date", value=client.current_song.upload_date, inline=True)
        except Exception as e:
            print(f"{e} for release date")
        try:
            embed.set_thumbnail(url=client.current_song.thumbnail)
        except Exception as e:
            print(f"{e} for thumbnail url")
        if (len(query) > 1024):
            query = query[:1024]
        embed.add_field(name="URL", value=query, inline=False)
        await interaction.response.send_message(embed=embed)
    else:
        msg = f":no_entry:There is no audio playing."
        embed = discord.Embed(description=f"*{msg}*")
        await interaction.response.send_message(embed=embed)

@client.tree.command()
async def help(interaction: discord.Interaction):
    """displays commands"""
    help_message = """
    `/play <url>` - Plays a song from a query that *can* be URL.
    `/pause` - Pauses the currently playing song.
    `/resume` - Resumes the currently paused song.
    `/stop` - Stops the currently playing song and clears the queue.
    `/skip` - Skips the currently playing song.
    `/queue` - Displays the current song queue.
    `/remove <position>` - Removes a song from the queue.
    `/clear` - Clears the song queue.
    `/volume <level>` - Sets the volume of the currently playing song.
    `/shuffle` - Shuffles the songs in the queue.
    `/loop <mode>` - Loops the currently playing song or the entire queue. Use 'song' or 'queue'.
    `/nowplaying` - Displays the currently playing song.
    `/seek <position>` - Seeks to a specific position in the currently playing song.
    `/history` - Shows the recently played songs.
    """
    embed = discord.Embed(title="Helpful Commands")
    embed.add_field(name="Descriptions",value=f"{help_message}", inline=False)
    embed.set_thumbnail(url="https://icons.iconarchive.com/icons/ph03nyx/super-mario/256/Retro-Block-Question-2-icon.png")
    await interaction.response.send_message(embed=embed)

client.run(os.environ['BOT_TOKEN'])