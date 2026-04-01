import { useState } from "react"
import { MessageCircle, Upload as UploadIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/hooks/useAuth"
import ChatWidget from "./ChatWidget"
import KnowledgeBasePanel from "./KnowledgeBasePanel"

export default function LandingPage() {
  const [chatOpen, setChatOpen] = useState(false)
  const [kbOpen, setKbOpen] = useState(false)
  const { isAuthenticated, signIn } = useAuth()

  if (!isAuthenticated) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-4 bg-gradient-to-br from-amber-50 to-orange-100">
        <h1 className="text-4xl font-bold text-gray-800">Restaurant Helper</h1>
        <p className="text-gray-600">Sign in to access the reservation assistant</p>
        <Button onClick={() => signIn()} size="lg">
          Sign In
        </Button>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-amber-50 via-orange-50 to-red-50">
      {/* Header */}
      <header className="bg-white shadow-sm">
        <div className="max-w-7xl mx-auto px-4 py-6 flex justify-between items-center">
          <h1 className="text-3xl font-bold text-orange-600">Restaurant Helper</h1>
          <nav className="space-x-6">
            <a href="#" className="text-gray-600 hover:text-orange-600">
              Home
            </a>
            <a href="#" className="text-gray-600 hover:text-orange-600">
              Restaurants
            </a>
            <a href="#" className="text-gray-600 hover:text-orange-600">
              About
            </a>
            <Button variant="outline" onClick={() => setKbOpen(!kbOpen)}>
              <UploadIcon className="h-4 w-4 mr-2" />
              Manage Docs
            </Button>
          </nav>
        </div>
      </header>

      {/* Hero Banner */}
      <section className="max-w-7xl mx-auto px-4 py-12 text-center">
        <h2 className="text-5xl font-bold text-gray-800 mb-6">Your Perfect Table Awaits</h2>
        <p className="text-xl text-gray-600 mb-8 max-w-2xl mx-auto">
          Discover and book the finest dining experiences in your city. Our AI assistant helps you
          find the perfect restaurant for any occasion.
        </p>
        <Button
          size="lg"
          className="bg-orange-600 hover:bg-orange-700"
          onClick={() => setChatOpen(true)}
        >
          Start Booking Now
        </Button>
      </section>

      {/* Features */}
      <section className="max-w-7xl mx-auto px-4 py-8 grid md:grid-cols-3 gap-8">
        <div className="bg-white p-8 rounded-lg shadow-md">
          <div className="text-4xl mb-4">🍽️</div>
          <h3 className="text-xl font-bold mb-2">Curated Selection</h3>
          <p className="text-gray-600">Access to premium restaurants across the city</p>
        </div>
        <div className="bg-white p-8 rounded-lg shadow-md">
          <div className="text-4xl mb-4">🤖</div>
          <h3 className="text-xl font-bold mb-2">AI Assistant</h3>
          <p className="text-gray-600">Get personalized recommendations instantly</p>
        </div>
        <div className="bg-white p-8 rounded-lg shadow-md">
          <div className="text-4xl mb-4">⚡</div>
          <h3 className="text-xl font-bold mb-2">Instant Booking</h3>
          <p className="text-gray-600">Reserve your table in seconds</p>
        </div>
      </section>

      {/* Featured Restaurants */}
      <section className="max-w-7xl mx-auto px-4 py-12">
        <h3 className="text-3xl font-bold text-center mb-12">Featured Restaurants</h3>
        <div className="grid md:grid-cols-3 gap-8">
          <div className="bg-white rounded-lg shadow-md overflow-hidden">
            <div
              className="h-48 bg-gradient-to-br from-orange-200 to-red-200 bg-cover bg-center"
              style={{ backgroundImage: "url('/images/golden-spoon.png')" }}
            />
            <div className="p-6">
              <h4 className="text-xl font-bold mb-2">The Golden Spoon</h4>
              <p className="text-gray-600 mb-4">
                Contemporary French cuisine with seasonal ingredients
              </p>
              <Button variant="outline" className="w-full">
                View Details
              </Button>
            </div>
          </div>
          <div className="bg-white rounded-lg shadow-md overflow-hidden">
            <div
              className="h-48 bg-gradient-to-br from-amber-200 to-orange-200 bg-cover bg-center"
              style={{ backgroundImage: "url('/images/botanic-table.png')" }}
            />
            <div className="p-6">
              <h4 className="text-xl font-bold mb-2">Botanic Table</h4>
              <p className="text-gray-600 mb-4">
                Farm-to-table dining with organic, locally sourced produce
              </p>
              <Button variant="outline" className="w-full">
                View Details
              </Button>
            </div>
          </div>
          <div className="bg-white rounded-lg shadow-md overflow-hidden">
            <div
              className="h-48 bg-gradient-to-br from-red-200 to-pink-200 bg-cover bg-center"
              style={{ backgroundImage: "url('/images/ember-oak.png')" }}
            />
            <div className="p-6">
              <h4 className="text-xl font-bold mb-2">Ember &amp; Oak</h4>
              <p className="text-gray-600 mb-4">
                Wood-fired steakhouse featuring prime cuts and craft cocktails
              </p>
              <Button variant="outline" className="w-full">
                View Details
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* Chat Widget */}
      {chatOpen && <ChatWidget onClose={() => setChatOpen(false)} />}

      {/* Knowledge Base Panel */}
      {kbOpen && <KnowledgeBasePanel onClose={() => setKbOpen(false)} />}

      {/* Floating Chat Button */}
      {!chatOpen && (
        <button
          onClick={() => setChatOpen(true)}
          className="fixed bottom-6 right-6 bg-orange-600 hover:bg-orange-700 text-white rounded-full p-4 shadow-lg transition-all hover:scale-110"
        >
          <MessageCircle className="h-6 w-6" />
        </button>
      )}
    </div>
  )
}
