import Link from 'next/link';

export default function About() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-ndl-blue to-blue-700 px-4 py-12">
      <div className="w-full max-w-2xl mx-auto">
        <div className="text-center mb-8">
          <div className="inline-block w-16 h-16 bg-white rounded-lg flex items-center justify-center mb-4">
            <span className="text-ndl-blue font-bold text-4xl">N</span>
          </div>
          <h1 className="text-4xl font-bold text-white mb-2">NDAY Route Manager</h1>
          <p className="text-blue-100">New Day Logistics LLC — internal operations platform</p>
        </div>

        <div className="bg-white rounded-lg shadow-2xl p-8 space-y-4 text-gray-700">
          <p>
            NDAY Route Manager is an internal operations platform built and used by
            New Day Logistics LLC, an Amazon Delivery Service Partner (DSP). It is not
            a public product or service — access is restricted to authorized New Day
            Logistics personnel and contractors.
          </p>

          <p>The platform supports day-to-day dispatch and operations work, including:</p>

          <ul className="list-disc list-inside space-y-1">
            <li>Daily route, van, and driver assignment</li>
            <li>Driver scheduling and attendance tracking</li>
            <li>Vehicle inspection (DVIC) and safety compliance tracking</li>
            <li>Rescue/assist event tracking and payroll bonus calculation</li>
            <li>Performance scorecard and invoice auditing against Amazon-provided data</li>
            <li>Hiring pipeline coordination — syncing job candidate information from
              Indeed into the company's Asana hiring board and internal contacts</li>
          </ul>

          <p>
            This site exists only to satisfy standard app information requirements for
            services (such as Google APIs) that this platform connects to on the
            company's behalf. See our{' '}
            <Link href="/privacy" className="text-ndl-blue underline">Privacy Policy</Link> and{' '}
            <Link href="/terms" className="text-ndl-blue underline">Terms of Service</Link>{' '}
            for details.
          </p>

          <p className="text-sm text-gray-500 pt-4 border-t border-gray-200">
            Questions? Contact{' '}
            <a href="mailto:jaysonwatson@newdaylogisticsllc.com" className="text-ndl-blue underline">
              jaysonwatson@newdaylogisticsllc.com
            </a>
            .
          </p>
        </div>
      </div>
    </div>
  );
}
