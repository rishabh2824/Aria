import {useState, useEffect, useRef} from 'react';
import { FiFileText, FiArrowRight } from 'react-icons/fi';
import { GridScan } from './components/GridScan.jsx';
import BlurText from "@/components/BlurText.jsx";
import ShinyText from "@/components/ShinyText.jsx";
import 'primereact/resources/themes/lara-light-indigo/theme.css';
import AiButton from "@/components/ai-button.js";
import Stepper, { Step } from "@/components/Stepper.jsx";
import {CircularProgress} from "@mui/material";

const AriaLanding = () => {
    const [formLink, setFormLink] = useState('');
    const [numResponses, setNumResponses] = useState('');
    const [targetAudience, setTargetAudience] = useState('');
    const [isSignedIn, setIsSignedIn] = useState(false);
    const [user, setUser] = useState(null);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [showGuidelines, setShowGuidelines] = useState(false);
    const googleButtonRef = useRef(null);
    const isDisabled = !targetAudience.trim() || !formLink.trim() || !numResponses;

    useEffect(() => {
        // Initialize Google Sign-In
        const initializeGoogleSignIn = () => {
            if (window.google && googleButtonRef.current) {
                try {
                    // Render the Google Sign-In button
                    window.google.accounts.id.initialize({
                        client_id: '491222888732-u2fd3bojldb39jk7alu2ccn8f6s4hhmv.apps.googleusercontent.com',
                        callback: handleCredentialResponse,
                        use_fedcm_for_prompt: false,
                    });

                    window.google.accounts.id.renderButton(
                        googleButtonRef.current,
                        {
                            theme: 'filled_black',
                            size: 'large',
                            text: 'signin_with',
                            shape: 'rectangular',
                            width: 280,
                        }
                    );
                } catch (error) {console.error('Error initializing Google Sign-In:', error);}
            }
        };

        if (window.google) {initializeGoogleSignIn();}
        else {
            const timer = setTimeout(initializeGoogleSignIn, 500);
            return () => clearTimeout(timer);
        }
    }, []);

    const handleCredentialResponse = async (response) => {
        try {
            // change to http://127.0.0.1:8000 in dev
            const result = await fetch('https://aria-l191.onrender.com/api/auth/google', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                },
                body: JSON.stringify({ token: response.credential })
            });

            const data = await result.json();

            if (!result.ok) {
                alert(data.detail || 'Authentication failed');
                return;
            }

            if (data.success) {
                setUser(data.user);
                setIsSignedIn(true);
            }
        } catch (error) {alert(`Failed to authenticate: ${error.message}`);}
    };

    const isFormValid = () => {
        if (!formLink.trim() || !numResponses.trim() || !targetAudience.trim()) {return false;}
        const num = parseInt(numResponses);
        return !(isNaN(num) || num < 1 || num > 20);
    };

    const handleSubmit = async () => {
        if (!isFormValid() || isSubmitting) return;

        setIsSubmitting(true);

        try {
            const response = await fetch(
                // change to http://127.0.0.1:8000 in dev
                'https://aria-l191.onrender.com/api/responses?format=csv',
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        form_link: formLink,
                        num_responses: parseInt(numResponses),
                        target_audience: targetAudience,
                    })
                }
            );

            if (!response.ok) {
                const text = await response.text();
                throw new Error(text || 'Failed to generate CSV');
            }

            // Convert response to Blob
            const blob = await response.blob();

            // Create a download link
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;

            // Optional: timestamped filename
            const timestamp = new Date().toISOString().split('T')[0];
            a.download = `ai_survey_responses_${timestamp}.csv`;

            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);

            // Reset form
            setFormLink('');
            setNumResponses('');
            setTargetAudience('');

        } catch (error) {
            alert(`Failed to generate responses: ${error.message}`);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="flex flex-col relative items-center justify-center h-screen w-full bg-[#0a0a1a] py-10">
            {/*Background*/}
            <div className="absolute inset-0 z-0">
                <GridScan
                    sensitivity={0}
                    lineThickness={1}
                    linesColor="#1a1a3e"
                    gridScale={0.1}
                    scanColor="#00ffff"
                    scanOpacity={0.2}
                    enablePost
                    bloomIntensity={0.6}
                    chromaticAberration={0.002}
                    noiseIntensity={0.01}
                />
            </div>

            {/* Title */}
            <BlurText
                text="Aria"
                delay={150}
                animateBy="letters"
                direction="top"
                className="text-7xl font-bold mb-5 text-[#e0e0ff] relative z-10"
            />

            {/* Subtitle */}
            <ShinyText
                text="The ultimate platform to simulate real world survey responses"
                speed={3}
                className="text-2xl font-semibold text-[#e0e0ff] relative z-10 mb-5"
            />

            {/* Google Sign-In Button */}
            {!isSignedIn && (
                <div className="relative z-10 flex flex-col items-center gap-4">
                    {/* Google's rendered button */}
                    <div ref={googleButtonRef}></div>
                    <p className="text-gray-400 text-sm">Sign in with Google to continue</p>
                </div>
            )}

            {/* Protected Content */}
            {isSignedIn && !showGuidelines && (
                <>
                    {/* Survey Guidelines */}
                    <div className="flex justify-center mt-6 mb-6 z-10">
                        <button
                            type="button"
                            onClick={() => setShowGuidelines(true)}
                            className="
                                group flex items-center gap-5 p-5 pr-8 bg-white/[0.03] border border-white/10
                                rounded-2xl transition-all duration-300 hover:border-purple-500/40
                                hover:bg-white/[0.07] hover:shadow-[0_0_30px_rgba(168,85,247,0.1)]
                            "
                        >
                            <div className="
                                flex items-center justify-center w-14 h-14 rounded-xl bg-purple-500/10
                                text-purple-400 group-hover:bg-purple-500/20 transition-colors">
                                <FiFileText size={28} />
                            </div>

                            <div className="text-left">
                                <h3 className="font-semibold text-gray-100 text-lg">Survey Quality Guidelines</h3>
                                <p className="text-sm text-gray-500">Ensure your survey meets simulation criteria</p>
                            </div>

                            <FiArrowRight className="
                                ml-4 text-gray-600 group-hover:text-purple-400 group-hover:translate-x-1
                                transition-all"
                                size={24}
                            />
                        </button>
                    </div>

                    {/*Form data input*/}
                    <div className="w-full max-w-2xl z-10 px-2">
                        <Stepper
                            initialStep={1}
                            backButtonText="Previous"
                            nextButtonText="Next"
                            stepCircleContainerClassName="bg-gradient-to-br from-[#1a1a3e] to-[#0f0f2a] border-[#3a3a6e]"
                            stepContainerClassName="bg-[#1a1a3e]/50"
                            contentClassName="text-gray-100"
                            footerClassName="bg-[#1a1a3e]/30"
                        >
                            <Step>
                                <h2 className="text-xl text-white mb-2">Enter the full link of your qualtrics survey</h2>
                                <input
                                    value={formLink}
                                    onChange={(e) => setFormLink(e.target.value)}
                                    placeholder="https://docs.google.com/forms/d/1oDPIjb5F9IS2TvfEanFZgCMaSNojLUZopgC1bOZ8D98/edit"
                                    className="
                                        w-full px-2 py-2 bg-[#0a0a1a] border border-[#3a3a6e] rounded-lg
                                        text-white placeholder-gray-500 focus:outline-none focus:border-purple-500
                                        focus:ring-2 focus:ring-purple-500/20 transition-all"
                                />
                            </Step>
                            <Step>
                                <h2 className="text-xl text-white mb-2">Enter the Number of responses to generate (1-20)</h2>
                                <input
                                    type="number"
                                    min="1"
                                    max="20"
                                    value={numResponses}
                                    onChange={(e) => setNumResponses(e.target.value)}
                                    placeholder="10 at a time Recommended"
                                    className="
                                    w-full px-2 py-2 bg-[#0a0a1a] border border-[#3a3a6e] rounded-lg text-white
                                    placeholder-gray-500 focus:outline-none focus:border-purple-500 focus:ring-2
                                    focus:ring-purple-500/20 transition-all"
                                />
                            </Step>
                            <Step>
                                <h2 className="text-xl text-white mb-2">Describe the persona of your Target Audience</h2>
                                <input
                                    value={targetAudience}
                                    onChange={(e) => setTargetAudience(e.target.value)}
                                    placeholder="College Students pursuing a degree in computer science..."
                                    className="
                                        w-full px-2 py-2 bg-[#0a0a1a] border border-[#3a3a6e] rounded-lg text-white
                                        placeholder-gray-500 focus:outline-none focus:border-purple-500 focus:ring-2
                                        focus:ring-purple-500/20 transition-all"
                                />
                            </Step>
                        </Stepper>
                    </div>

                    <div className={isDisabled || isSubmitting ? 'opacity-50 pointer-events-none' : ''}>
                        <AiButton onClick={handleSubmit} />
                    </div>

                    {isSubmitting && (
                        <CircularProgress />
                    )}
                </>
            )}

            {isSignedIn && showGuidelines && (
                <div className="relative z-10 w-full max-w-3xl px-6">
                    <div className="bg-white/3 border border-white/10 rounded-2xl p-6">
                        <h2 className="text-2xl font-semibold text-gray-100 mb-4">Survey Guidelines</h2>
                        <ul className="list-disc list-inside text-gray-300 space-y-2">
                            <li>Make sure to share your survey with full edit access to rvjain@wisc.edu</li>
                            <li>There should be no Javascript, embedded data, or piped text throughout the survey.</li>
                            <li>No files or images should be used as part of a question or response.</li>
                            <li>If a multiple choice or checkbox has "Other" as an option, do not allow free-text entry for it.</li>
                            <li>For any questions expecting a text response, specify a word-range in the question itself.</li>
                            <li>Please avoid question types other than multiple choice, text and slider.</li>
                        </ul>

                        <div className="mt-6">
                            <button
                                type="button"
                                onClick={() => setShowGuidelines(false)}
                                className="px-4 py-2 rounded-lg bg-purple-500/20 text-purple-200 border border-purple-500/40 hover:bg-purple-500/30 transition-colors"
                            >
                                Back to Home
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default AriaLanding;
