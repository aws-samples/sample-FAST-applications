# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import logging
import os
import uuid
from datetime import datetime

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_booking_details(booking_id: str, restaurant_name: str) -> dict:
    """Get the relevant details for booking_id in restaurant_name."""
    try:
        table_name = os.environ.get("RESERVATIONS_TABLE_NAME")
        if not table_name:
            return {"error": "Reservations table name not configured"}

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)

        response = table.get_item(
            Key={"booking_id": booking_id, "restaurant_name": restaurant_name}
        )

        if "Item" in response:
            item = response["Item"]
            result = "Booking Details:\n"
            result += f"Booking ID: {item.get('booking_id', 'N/A')}\n"
            result += f"Restaurant: {item.get('restaurant_name', 'N/A')}\n"
            result += f"Customer: {item.get('customer_name', 'N/A')}\n"
            result += f"Party Size: {item.get('party_size', 'N/A')}\n"
            result += f"Date/Time: {item.get('reservation_time', 'N/A')}\n"
            result += f"Phone: {item.get('phone_number', 'N/A')}\n"
            return {"success": True, "message": result}
        else:
            return {
                "error": f"No booking found with ID {booking_id} at {restaurant_name}"
            }

    except Exception as e:
        logger.error(f"Error retrieving booking: {str(e)}")
        return {"error": f"Failed to retrieve booking: {str(e)}"}


def create_booking(
    date: str,
    hour: str,
    restaurant_name: str,
    guest_name: str,
    num_guests: int,
    phone_number: str = None,
) -> dict:
    """Create a new restaurant reservation."""
    try:
        table_name = os.environ.get("RESERVATIONS_TABLE_NAME")
        if not table_name:
            return {"error": "Reservations table name not configured"}

        booking_id = f"booking-{uuid.uuid4().hex[:8]}"
        reservation_time = f"{date}T{hour}:00"

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)

        item = {
            "booking_id": booking_id,
            "restaurant_name": restaurant_name,
            "customer_name": guest_name,
            "party_size": num_guests,
            "reservation_time": reservation_time,
            "created_at": datetime.utcnow().isoformat(),
        }

        if phone_number:
            item["phone_number"] = phone_number

        response = table.put_item(Item=item)

        if response["ResponseMetadata"]["HTTPStatusCode"] == 200:
            result = f"✅ Reservation created successfully for {num_guests} people at {restaurant_name} on {date} at {hour} in the name of {guest_name}\n"
            result += f"📋 Booking ID: {booking_id}\n"
            result += "Please save this booking ID for future reference."
            return {"success": True, "message": result}
        else:
            return {"error": "Failed to create reservation"}

    except Exception as e:
        logger.error(f"Error creating booking: {str(e)}")
        return {"error": f"Failed to create booking: {str(e)}"}


def delete_booking(booking_id: str, restaurant_name: str) -> dict:
    """Delete an existing booking_id at restaurant_name."""
    try:
        table_name = os.environ.get("RESERVATIONS_TABLE_NAME")
        if not table_name:
            return {"error": "Reservations table name not configured"}

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)

        # Check if booking exists first
        get_response = table.get_item(
            Key={"booking_id": booking_id, "restaurant_name": restaurant_name}
        )

        if "Item" not in get_response:
            return {
                "error": f"No booking found with ID {booking_id} at {restaurant_name}"
            }

        # Delete the item
        response = table.delete_item(
            Key={"booking_id": booking_id, "restaurant_name": restaurant_name}
        )

        if response["ResponseMetadata"]["HTTPStatusCode"] == 200:
            return {
                "success": True,
                "message": f"✅ Booking with ID {booking_id} at {restaurant_name} has been successfully cancelled",
            }
        else:
            return {"error": "Failed to delete booking"}

    except Exception as e:
        logger.error(f"Error deleting booking: {str(e)}")
        return {"error": f"Failed to delete booking: {str(e)}"}


def handler(event, context):
    """
    Reservation tools Lambda function for AgentCore Gateway.

    Routes to the appropriate tool handler based on the tool name
    extracted from the Lambda invocation context.

    Args:
        event: Tool input parameters (booking_id, restaurant_name, etc.)
        context: Lambda context with client_context.custom["bedrockAgentCoreToolName"]

    Returns:
        dict: Tool result with either "content" or "error" key.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        # Get tool name from context and strip the target prefix
        delimiter = "___"
        original_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
        tool_name = original_tool_name[
            original_tool_name.index(delimiter) + len(delimiter) :
        ]

        logger.info(f"Processing tool: {tool_name}")

        # Route to appropriate tool handler
        if tool_name == "get_booking_details":
            booking_id = event.get("booking_id", "")
            restaurant_name = event.get("restaurant_name", "")

            if not booking_id or not restaurant_name:
                return {"error": "Both booking_id and restaurant_name are required"}

            result = get_booking_details(booking_id, restaurant_name)

        elif tool_name == "create_booking":
            date = event.get("date", "")
            hour = event.get("hour", "")
            restaurant_name = event.get("restaurant_name", "")
            guest_name = event.get("guest_name", "")
            num_guests = event.get("num_guests")
            phone_number = event.get("phone_number")

            if not all([date, hour, restaurant_name, guest_name, num_guests]):
                return {
                    "error": "Missing required fields: date, hour, restaurant_name, guest_name, num_guests"
                }

            result = create_booking(
                date, hour, restaurant_name, guest_name, num_guests, phone_number
            )

        elif tool_name == "delete_booking":
            booking_id = event.get("booking_id", "")
            restaurant_name = event.get("restaurant_name", "")

            if not booking_id or not restaurant_name:
                return {"error": "Both booking_id and restaurant_name are required"}

            result = delete_booking(booking_id, restaurant_name)

        else:
            logger.error(f"Unexpected tool name: {tool_name}")
            return {
                "error": f"Unsupported tool: {tool_name}. Supported tools: get_booking_details, create_booking, delete_booking"
            }

        # Handle result
        if "error" in result:
            return {"error": result["error"]}
        else:
            return {"content": [{"type": "text", "text": result["message"]}]}

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return {"error": f"Internal server error: {str(e)}"}
