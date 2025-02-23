# Function to run the agent in a new process
import asyncio
import logging
import os
import signal
from multiprocessing import Process
from typing import Optional
import sys

from aiohttp import web
from dotenv import load_dotenv

from .realtime.struct import PCM_CHANNELS, PCM_SAMPLE_RATE, ServerVADUpdateParams, Voices

from .agent import InferenceConfig, ElevenLabsAgent
from agora_realtime_ai_api.rtc import RtcEngine, RtcOptions
from .logger import setup_logger
from .parse_args import parse_args, parse_args_realtimekit
from .config import ElevenLabsConfig

# Set up the logger with color and timestamp support
logger = setup_logger(name=__name__, log_level=logging.INFO)

load_dotenv(override=True)
app_id = os.environ.get("AGORA_APP_ID")
app_cert = os.environ.get("AGORA_APP_CERT")

if not app_id:
    raise ValueError("AGORA_APP_ID must be set in the environment.")


# Function to monitor the process and perform extra work when it finishes
async def monitor_process(channel_name: str, process: Process):
    # Wait for the process to finish in a non-blocking way
    await asyncio.to_thread(process.join)

    logger.info(f"Process for channel {channel_name} has finished")

    # Perform additional work after the process finishes
    # For example, removing the process from the active_processes dictionary
    if channel_name in active_processes:
        active_processes.pop(channel_name)

    # Perform any other cleanup or additional actions you need here
    logger.info(f"Cleanup for channel {channel_name} completed")

    logger.info(f"Remaining active processes: {len(active_processes.keys())}")

def handle_agent_proc_signal(signum, frame):
    logger.info(f"Agent process received signal {signal.strsignal(signum)}. Exiting...")
    os._exit(0)


def run_agent_in_process(
    engine_app_id: str,
    engine_app_cert: str,
    channel_name: str,
    uid: int,
    config: ElevenLabsConfig,
):
    """Run the agent in a process"""
    signal.signal(signal.SIGINT, handle_agent_proc_signal)
    signal.signal(signal.SIGTERM, handle_agent_proc_signal)
    
    async def run():
        # Load config if not provided
        try:
            agent_config = config or ElevenLabsConfig.from_env()
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            return
            
        engine = RtcEngine(appid=engine_app_id, appcert=engine_app_cert)
        options = RtcOptions(
            channel_name=channel_name,
            uid=uid,
            sample_rate=PCM_SAMPLE_RATE,
            channels=PCM_CHANNELS,
            enable_pcm_dump=os.environ.get("WRITE_PCM", "false") == "true"
        )
        
        channel = engine.create_channel(options)
        await channel.connect()
        
        try:
            agent = ElevenLabsAgent(
                channel=channel,
                config=agent_config,
                tools=None  # Add tools if needed
            )
            await agent.run()
        finally:
            await channel.disconnect()
    
    asyncio.run(run())


# HTTP Server Routes
async def start_agent(request):
    try:
        data = await request.json()
        
        # Extract and validate data directly
        channel_name = data.get("channel_name")
        uid = data.get("uid")
        voice_id = data.get("voice_id")
        model = data.get("model")
        agent_id = data.get("agent_id")
        
        if not channel_name or not uid:
            return web.json_response(
                {"error": "channel_name and uid are required fields"},
                status=400,
            )
        
        if channel_name in active_processes and active_processes[channel_name].is_alive():
            return web.json_response(
                {"error": f"Agent already running for channel: {channel_name}"},
                status=400,
            )

        # Load ElevenLabs config
        try:
            config = ElevenLabsConfig.from_env()
            
            # Override with request values if provided
            if voice_id:
                config.voice_id = voice_id
            if model:
                config.model = model
            if agent_id:
                config.agent_id = agent_id
                
                
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        process = Process(
            target=run_agent_in_process,
            args=(app_id, app_cert, channel_name, uid, config),
        )

        try:
            process.start()
        except Exception as e:
            logger.error(f"Failed to start agent process: {e}")
            return web.json_response(
                {"error": f"Failed to start agent: {e}"}, status=500
            )

        active_processes[channel_name] = process
        asyncio.create_task(monitor_process(channel_name, process))

        return web.json_response({
            "status": "Agent started",
            "config": {
                "voice_id": config.voice_id,
                "model": config.model,
                "agent_id": config.agent_id
            }
        })

    except Exception as e:
        logger.error(f"Failed to start agent: {e}")
        return web.json_response({"error": str(e)}, status=500)


# HTTP Server Routes: Stop Agent
async def stop_agent(request):
    try:
        data = await request.json()
        
        # Extract and validate data directly
        channel_name = data.get("channel_name")
        
        if not channel_name:
            return web.json_response(
                {"error": "channel_name is a required field"},
                status=400,
            )
        
        # Find and terminate the process associated with the given channel name
        process = active_processes.get(channel_name)

        if process and process.is_alive():
            logger.info(f"Terminating process for channel {channel_name}")
            await asyncio.to_thread(os.kill, process.pid, signal.SIGKILL)

            return web.json_response(
                {"status": "Agent process terminated", "channel_name": channel_name}
            )
        else:
            return web.json_response(
                {"error": "No active agent found for the provided channel_name"},
                status=404,
            )

    except Exception as e:
        logger.error(f"Failed to stop agent: {e}")
        return web.json_response({"error": str(e)}, status=500)


# Dictionary to keep track of processes by channel name or UID
active_processes = {}


# Function to handle shutdown and process cleanup
async def shutdown(app):
    logger.info("Shutting down server, cleaning up processes...")
    for channel_name in list(active_processes.keys()):
        process = active_processes.get(channel_name)
        if process.is_alive():
            logger.info(
                f"Terminating process for channel {channel_name} (PID: {process.pid})"
            )
            await asyncio.to_thread(os.kill, process.pid, signal.SIGKILL)
            await asyncio.to_thread(process.join)  # Ensure process has terminated
    active_processes.clear()
    logger.info("All processes terminated, shutting down server")


# Signal handler to gracefully stop the application
def handle_signal(signum, frame):
    logger.info(f"Received exit signal {signal.strsignal(signum)}...")

    loop = asyncio.get_running_loop()
    if loop.is_running():
        # Properly shutdown by stopping the loop and running shutdown
        loop.create_task(shutdown(None))
        loop.stop()


# Main aiohttp application setup
async def init_app():
    app = web.Application()

    # Add cleanup task to run on app exit
    app.on_cleanup.append(shutdown)

    app.add_routes([
        web.post("/start_agent", start_agent),
        web.post("/stop_agent", stop_agent),
        web.get("/health", health_check),
        web.get("/agent_status", agent_status)
    ])

    return app


# Update the command-line agent runner
def run_cli_agent(options: dict):
    """Run agent from command line with given options"""
    try:
        config = ElevenLabsConfig.from_env()
        # Log the config to verify values are loaded (but mask the API key)
        logger.info(f"Loaded config: voice_id={config.voice_id}, model={config.model}, agent_id={config.agent_id}")
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    run_agent_in_process(
        engine_app_id=app_id,
        engine_app_cert=app_cert,
        channel_name=options["channel_name"],
        uid=options["uid"],
        config=config
    )


async def health_check(request):
    """Basic health check endpoint"""
    return web.json_response({
        "status": "healthy",
        "active_agents": len(active_processes)
    })

async def agent_status(request):
    """Get status of specific agent"""
    try:
        channel_name = request.query.get("channel_name")
        if not channel_name:
            return web.json_response(
                {"error": "channel_name parameter is required"}, 
                status=400
            )
            
        process = active_processes.get(channel_name)
        if not process:
            return web.json_response(
                {"status": "not_found", "channel_name": channel_name}
            )
            
        return web.json_response({
            "status": "running" if process.is_alive() else "stopped",
            "channel_name": channel_name,
            "pid": process.pid
        })
        
    except Exception as e:
        logger.error(f"Error checking agent status: {e}")
        return web.json_response({"error": str(e)}, status=500)


if __name__ == "__main__":
    # Parse the action argument
    args = parse_args()
    
    # Add debug logging for environment variables
    logger.info("Checking environment variables:")
    logger.info(f"ELEVENLABS_API_KEY exists: {bool(os.getenv('ELEVENLABS_API_KEY'))}")
    logger.info(f"ELEVENLABS_VOICE_ID: {os.getenv('ELEVENLABS_VOICE_ID')}")
    logger.info(f"ELEVENLABS_MODEL: {os.getenv('ELEVENLABS_MODEL')}")
    logger.info(f"ELEVENLABS_AGENT_ID: {os.getenv('ELEVENLABS_AGENT_ID')}")
    
    # Make sure dotenv is loaded
    load_dotenv(override=True)
    
    # Action logic based on the action argument
    if args.action == "server":
        # Python 3.10+ requires explicitly creating a new event loop if none exists
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # For Python 3.10+, use this to get a new event loop if the default is closed or not created
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Start the application using asyncio.run for the new event loop
        app = loop.run_until_complete(init_app())
        web.run_app(app, port=int(os.getenv("SERVER_PORT") or "8080"))
    elif args.action == "agent":
        # Parse RealtimeKitOptions for running the agent
        realtime_kit_options = parse_args_realtimekit()

        # Example logging for parsed options (channel_name and uid)
        logger.info(f"Running agent with options: {realtime_kit_options}")
        
        try:
            # Try loading config from environment first
            config = ElevenLabsConfig.from_env()
            logger.info("Successfully loaded config from environment")
        except ValueError as e:
            logger.warning(f"Could not load config from environment: {e}")
            # Fall back to explicit loading
            config = ElevenLabsConfig(
                api_key=os.getenv("ELEVEN_LABS_API_KEY"),
                voice_id=os.getenv("ELEVEN_LABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),  # Default voice
                model=os.getenv("ELEVEN_LABS_MODEL", "eleven_monolingual_v1"),  # Default model
                agent_id=os.getenv("ELEVEN_LABS_AGENT_ID", "eleven_labs_demo")  # Default agent
            )
        
        # Log config for debugging (mask the API key)
        logger.info(f"Using config: voice_id={config.voice_id}, model={config.model}, agent_id={config.agent_id}")
        
        if not config.api_key:
            logger.error("No API key found in environment variables!")
            sys.exit(1)
            
        run_agent_in_process(
            engine_app_id=app_id,
            engine_app_cert=app_cert,
            channel_name=realtime_kit_options["channel_name"],
            uid=realtime_kit_options["uid"],
            config=config
        )
