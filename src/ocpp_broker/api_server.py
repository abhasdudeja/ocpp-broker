import asyncio
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn
from .tag_api import create_tag_api
from .ocpp_command_api import create_ocpp_command_api

logger = logging.getLogger("ocpp_broker.api")

class BackendModel(BaseModel):
    id: str
    url: str
    leader: Optional[bool] = False

def create_api(broker):
    """
    Create FastAPI app bound to a running OcppBroker instance.
    """
    app = FastAPI(title="OCPP Broker API", version="1.0")
    
    # Include tag management API if available
    tag_router = create_tag_api(broker)
    app.include_router(tag_router)
    
    # Include OCPP command API
    ocpp_command_router = create_ocpp_command_api(broker)
    app.include_router(ocpp_command_router)

    @app.get("/orgs")
    async def list_orgs():
        return [
            {
                "name": org,
                "num_backends": len(broker.org_backends.get(org, {})),
                "leader": getattr(broker.org_leaders.get(org), "id", None)
            }
            for org in broker.org_backends.keys()
        ]

    @app.get("/orgs/{org}/backends")
    async def list_backends(org: str):
        backs = broker.org_backends.get(org)
        if backs is None:
            raise HTTPException(status_code=404, detail="Organization not found")
        return [
            {
                "id": b.id,
                "url": b.url,
                "leader": b.is_leader,
                "connected": (b.websocket is not None and not getattr(b.websocket, "closed", True))
            }
            for b in backs.values()
        ]

    @app.post("/orgs/{org}/backends")
    async def add_backend(org: str, backend: BackendModel):
        if org not in broker.org_backends:
            raise HTTPException(status_code=404, detail="Organization not found")
        if backend.id in broker.org_backends[org]:
            raise HTTPException(status_code=400, detail="Backend already exists")

        # add backend asynchronously
        await broker.add_backend_dynamic(org, backend.model_dump())
        return {"status": "created", "backend": backend.id}

    @app.delete("/orgs/{org}/backends/{backend_id}")
    async def remove_backend(org: str, backend_id: str):
        if org not in broker.org_backends:
            raise HTTPException(status_code=404, detail="Organization not found")
        removed = await broker.remove_backend_dynamic(org, backend_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Backend not found")
        return {"status": "removed", "backend": backend_id}

    @app.post("/orgs/{org}/leader/{backend_id}")
    async def set_leader(org: str, backend_id: str):
        if org not in broker.org_backends:
            raise HTTPException(status_code=404, detail="Organization not found")
        success = broker.promote_leader(org, backend_id)
        if not success:
            raise HTTPException(status_code=404, detail="Backend not found")
        return {"status": "leader_updated", "leader": backend_id}

    @app.post("/reload")
    async def reload_config():
        await broker.reload_from_config()
        return {"status": "config_reloaded"}

    return app


async def start_api(broker, host="0.0.0.0", port=8080):
    """
    Launch FastAPI in background using uvicorn Server. This returns immediately and runs the server task.
    """
    app = create_api(broker)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    loop = asyncio.get_event_loop()
    # run uvicorn in background
    loop.create_task(server.serve())
    logger.info(f"API Server running at http://{host}:{port}")
