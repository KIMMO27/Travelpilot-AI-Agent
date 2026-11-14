# TravelPilot AI

### Intelligent Travel Planning Agent

TravelPilot AI is an LLM-powered travel planning agent developed with Python, LangGraph, DeepSeek, Geoapify and OpenStreetMap data.

## Features

- Personalized multi-day itinerary generation
- Real-world place discovery
- Walking distance and time estimation
- LangGraph-based planning and verification workflow
- Multi-turn conversations and persistent user preferences
- Multi-model API support and automatic fallback
- Tool execution tracing and error handling
- Grounded responses with map references

## Architecture

User Request → LangGraph → Parse Requirements → Plan Trip → Verify Results → Generate Itinerary

The project also includes a conversational LLM Agent that can select and call tools.

## Technology Stack

Python, LangGraph, DeepSeek API, Geoapify API, OpenStreetMap, Git, unittest

## Installation

Install the dependencies listed in requirements.txt.

Configure API credentials locally using environment variables or a .env file. Never commit real API keys.

## Running the Project

Run the LangGraph planner using the travel_agent.graph_cli module.

Set TRAVEL_MAP_PROVIDER to geoapify and TRAVEL_DATA_MODE to osm.

Example question:

"Plan a 3-day trip to Melbourne with a daily budget of AUD 150. I enjoy coffee."

## Evaluation Results

- 36 automated tests passed.
- 3 out of 3 evaluation scenarios passed.
- 100% place candidate coverage in the tested 9-slot Melbourne itinerary.
- 100% route availability in the tested 3-day itinerary.
- Final map links matched retrieved tool results.

These measurements apply to the evaluated scenarios, not overall production accuracy.

## Limitations

- Business opening hours and real-time prices are not verified.
- Walking times are estimates.
- Budget compliance requires verified pricing.
- Real bookings and reservations are not supported.

## Documentation

- Travel Agent Architecture: docs/travel_agent.md
- Evaluation Results: docs/travel_evaluation.md

## Status

Functional portfolio prototype, tested locally on macOS.
