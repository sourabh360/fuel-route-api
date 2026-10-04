import time
from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.geocoding import GeocodingError
from .services.optimizer import NoFuelPlanError
from .services.planner import build_plan
from .services.routing import RoutingError


def get_plan(params):
    serializer = RouteRequestSerializer(data=params)
    if not serializer.is_valid():
        return None, Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    try:
        return build_plan(data["start"], data["finish"]), None
    except GeocodingError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except NoFuelPlanError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except RoutingError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


class FuelRouteView(APIView):

    def get(self, request):
        return self.handle(request, request.query_params)

    def post(self, request):
        return self.handle(request, request.data)

    def handle(self, request, params):
        started = time.perf_counter()
        plan, error = get_plan(params)
        if error:
            return error

        query = urlencode({"start": plan["start"]["query"], "finish": plan["finish"]["query"]})
        map_url = request.build_absolute_uri(f"{reverse('route-map')}?{query}")
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        return Response({**plan, "map_url": map_url, "response_time_ms": elapsed})


class FuelRouteMapView(APIView):

    def get(self, request):
        plan, error = get_plan(request.query_params)
        if error:
            return error
        return render(request, "routing/map.html", {"plan": plan})
