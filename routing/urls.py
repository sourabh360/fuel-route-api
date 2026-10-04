from django.urls import path

from .views import FuelRouteMapView, FuelRouteView

urlpatterns = [
    path("route/", FuelRouteView.as_view(), name="route"),
    path("route/map/", FuelRouteMapView.as_view(), name="route-map"),
]
