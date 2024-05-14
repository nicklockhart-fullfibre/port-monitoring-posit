import aiohttp
import asyncio
import csv
import datetime
import io
import json
import pandas as pd

from shiny import render, reactive, req, types
from shiny.express import input, output, ui

from shiny import ui as s_ui
from shiny import render as s_render

from shinyswatch import theme

example_data = pd.DataFrame(
    [
        {
            "host": "127.0.0.1",
            "ifName": "eth0",
            "ifDescr": "ethernet"
        }
    ]
)

# theme.superhero()

ui.h1("Live Port Monitoring")

ui.div(
    ui.input_action_button(
        "auth",
        "Fetch Data"
    )
)

ui.br()

@render.text
async def table_caption():
    await req(fetch_lnms_data())
    return "The following ports are ignored but up:"

@render.data_frame
async def generate_dev_accordion():
    await req(fetch_lnms_data())
    port_data = await fetch_lnms_data() # type: ignore

    return render.DataGrid(
        pd.DataFrame(port_data),
        width="100%",
        height="70vh",
        summary="Viewing ports {start} through {end} of {total}",
        filters=True,
        selection_mode="none"
    )

@render.ui
async def show_dl_maybe():
    await req(fetch_lnms_data())
    port_data = await fetch_lnms_data() # type: ignore

    dl_button = s_ui.download_button("export_button", "Export data")

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")

    @s_render.download(
            filename=f"{timestamp} Port Monitoring Data.csv",
            media_type="text/csv"
    )
    def export_button():
        with io.StringIO() as buf:
            keys = port_data[0].keys()
            dw = csv.DictWriter(buf, keys)
            dw.writeheader()
            for row in port_data:
                dw.writerow(row)
            yield buf.getvalue()

    return dl_button


@reactive.effect
@reactive.event(input.auth)
def show_lnms_cred_prompt():
    m = ui.modal(
        ui.input_text("lnms_inst", "LibreNMS Instance URL:"),
        ui.input_password("lnms_key", "LibreNMS API Key:"),
        ui.input_action_button("fetch", "Fetch Data"),
        title="LibreNMS Credentials",
        easy_close=True,
        footer=None
    )
    ui.modal_show(m)


@reactive.calc
@reactive.event(input.fetch)
async def fetch_lnms_data():
    ui.modal_remove()
    if input.lnms_inst() == "":
        ui.notification_show(
            "No LibreNMS instance specified!",
            type="error",
            duration=5
        )
        return
    if input.lnms_key() == "":
        ui.notification_show(
            "No LibreNMS API key specified!",
            type="error",
            duration=5
        )
        return

    base_url = f"{input.lnms_inst()}/api/v0"
    api_header = {"X-Auth-Token": input.lnms_key()}
    aio_timeout = aiohttp.ClientTimeout(10) # set timeout to ten seconds for session
    async with aiohttp.ClientSession(headers=api_header, timeout=aio_timeout) as session:
        ports_resp = await session.get(
            f"{base_url}/ports",
            params={
                "columns": "ifName,ifAlias,device_id,ignore,ifOperStatus,ifAdminStatus"
            }
        )
        if ports_resp.status != 200:
            ui.notification_show(
                f"Failed to fetch port data: {ports_resp.status} ({ports_resp.reason})",
                type="error",
                duration=5
            )
            return
        else:
            port_body = await ports_resp.text()
            port_info = json.loads(port_body)
            
            ignored_ports = [
                port for port in port_info['ports']
                if port['ignore'] == 1
                and port['ifOperStatus'] == 'up'
                and port['ifAdminStatus'] == 'up'
            ]

            ignored_device_ids = list(set(
                port['device_id'] for port in ignored_ports
            ))

            device_table = await fetch_device_data(
                ignored_device_ids, 
                session, 
                base_url
            )
            ret_data = []
            
            for device_id, device in device_table.items():
                hostname = device['hostname']
                sysname = device['sysName']

                ignored_ports_on_device = [
                    port for port in ignored_ports
                    if port['device_id'] == device_id
                ]
                for port in ignored_ports_on_device:
                    port_name = port['ifName']
                    port_alias = port['ifAlias']
                    oper_status = port['ifOperStatus']
                    admin_status = port['ifAdminStatus']

                    ret_data.append(
                        {
                            "Hostname": hostname,
                            "Sysname": sysname,
                            "Port Name": port_name,
                            "Port Alias": port_alias
                        }
                    )


            return ret_data

async def fetch_device_data(device_ids: list, session: aiohttp.ClientSession, base_url: str):
    device_table = {}
    with ui.Progress(min=0, max=len(device_ids)) as p:
        p.set(message="Fetching device data", detail="This may take a while")
        for pos, device_id in enumerate(device_ids):
            p.set(pos)
            device_resp = await session.get(f"{base_url}/devices/{device_id}")
            if device_resp.status != 200:
                ui.notification_show(
                    f"Failed to fetch data for device {device_id}: {device_resp.status} ({device_resp.reason})",
                    type="warning",
                    duration=5
                )
            else:
                device_data_raw = await device_resp.text()
                device_info_raw = json.loads(device_data_raw)
                if "devices" in device_info_raw:
                    device_info = device_info_raw['devices'][0]
                    device_table[device_id] = device_info

    ui.notification_show(
        f"Fetched data for {len(device_ids)} devices",
        type="message",
        duration=5
    )
    
    return device_table

