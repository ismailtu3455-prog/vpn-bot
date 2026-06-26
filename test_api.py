import asyncio
from bot.services.vpn import get_panel_api
from pprint import pprint

async def test():
    panel = get_panel_api("germany")
    # we need an existing email. Let's try to find an existing client.
    # We will get all clients from inbound 1
    inbounds = await panel.get_inbounds()
    for inbound in inbounds:
        print(f"Inbound ID: {inbound['id']}, Protocol: {inbound['protocol']}, Port: {inbound['port']}")
        
    client_info = await panel.get_client("ismailtu3455-prog") # maybe he doesn't have this.
    # let's just get the first client from inbound 1
    if inbounds:
        first_inbound = inbounds[0]
        settings = first_inbound.get("settings", "{}")
        import json
        try:
            settings_obj = json.loads(settings)
            clients = settings_obj.get("clients", [])
            if clients:
                email = clients[0].get("email")
                print(f"Found email: {email}")
                links = await panel.get_client_links(email)
                print("Links for client:")
                pprint(links)
        except Exception as e:
            print("Error parsing settings", e)

asyncio.run(test())
