import asyncio
import logging
from typing import Any, Optional

import typer
from aiohttp import web
from asyncua import Server as OpcServer
from asyncua import ua
from asyncua.common.type_dictionary_builder import DataTypeDictionaryBuilder
from asyncua.server.users import User, UserRole
from asyncua.ua.uatypes import NodeId

NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"


class UserManager:
    def get_user(
        self,
        iserver: Any,
        username: Optional[str],
        password: Optional[str],
        certificate: Optional[Any],
    ) -> Optional[User]:
        del iserver
        del certificate
        if (
            username == "authorized_user"
            and password == "authorized_password"  # noqa: S105
        ):
            return User(UserRole.User)
        return None


class TestOpcServer:
    def __init__(self) -> None:
        self.opc_server = OpcServer(user_manager=UserManager())

    async def ping_handler(
        self,
        request: web.Request,  # noqa: U100
    ) -> web.Response:
        return web.Response(text="PONG")

    async def api_delete_handler(
        self,
        request: web.Request,  # noqa: U100
    ) -> web.Response:
        await self.reset_opc_data()
        return web.Response()

    async def change_node_handler(self, request: web.Request) -> web.Response:
        request_kind = request.query["kind"]
        if request_kind == "monitored":
            var = ua.MonitoredStructure()
            var.Name = "A changed name"
            var.Id = 84
            await self.monitored_var.write_value(var)
        elif request_kind == "recorded":
            vars = []
            for index in range(2):
                var = ua.RecordedStructure()
                var.Age = [67, 12][index]
                var.Active = [False, True][index]
                vars.append(var)
            await self.recorded_var.write_value(vars)
        else:
            raise web.HTTPBadRequest(reason="Unknown kind parameter")
        return web.Response()

    async def get_subscriptions_handler(
        self,
        request: web.Request,  # noqa: U100
    ) -> web.Response:
        return web.json_response(
            list(self.opc_server.iserver.subscription_service.subscriptions)
        )

    async def reset_opc_data(self) -> None:
        var = ua.MonitoredStructure()
        var.Name = "A name"
        var.Id = 42
        await self.monitored_var.write_value(var)
        vars = []
        for index in range(2):
            var = ua.RecordedStructure()
            var.Age = [18, 32][index]
            var.Active = [True, False][index]
            vars.append(var)
        await self.recorded_var.write_value(vars)

    async def run(self, http_port: int) -> None:
        await self.opc_server.init()

        self.opc_server.set_security_policy(
            [
                ua.SecurityPolicyType.Basic256Sha256_Sign,
                ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
            ]
        )
        await self.opc_server.load_certificate("test-server-cert.der")
        await self.opc_server.load_private_key("test-server-key.pem")

        idx = await self.opc_server.register_namespace(NAMESPACE_URI)

        dict_builder = DataTypeDictionaryBuilder(
            self.opc_server, idx, NAMESPACE_URI, "SimaticStructures"
        )
        await dict_builder.init()

        monitored_structure = await dict_builder.create_data_type("MonitoredStructure")
        monitored_structure.add_field("Name", ua.VariantType.String)
        monitored_structure.add_field("Id", ua.VariantType.Int32)

        recorded_structure = await dict_builder.create_data_type("RecordedStructure")
        recorded_structure.add_field("Age", ua.VariantType.Int16)
        recorded_structure.add_field("Active", ua.VariantType.Boolean)

        await dict_builder.set_dict_byte_string()
        await self.opc_server.load_type_definitions()

        self.monitored_var = await self.opc_server.nodes.objects.add_variable(
            NodeId("Monitored", idx),
            "Monitored",
            None,
            datatype=monitored_structure.data_type,
        )

        self.recorded_var = await self.opc_server.nodes.objects.add_variable(
            NodeId("Recorded", idx),
            "Recorded",
            None,
            datatype=recorded_structure.data_type,
        )

        await self.reset_opc_data()

        app = web.Application()
        app.add_routes(
            [
                web.get("/ping", self.ping_handler),
                web.delete("/api", self.api_delete_handler),
                web.post("/api/node", self.change_node_handler),
                web.get("/api/subscriptions", self.get_subscriptions_handler),
            ]
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, port=http_port)
        await site.start()

        async with self.opc_server:
            await asyncio.sleep(3600)


def main(http_port: int) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    test_opc_server = TestOpcServer()
    asyncio.get_event_loop().run_until_complete(test_opc_server.run(http_port))


if __name__ == "__main__":
    typer.run(main)
