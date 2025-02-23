import React from 'react';

const Background = () => {
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute top-0 -left-4 w-72 h-72 bg-purple-500 rounded-full mix-blend-multiply filter blur-xl opacity-10 animate-float"></div>
      <div className="absolute top-0 -right-4 w-72 h-72 bg-yellow-500 rounded-full mix-blend-multiply filter blur-xl opacity-10 animate-float" style={{ animationDelay: '2s' }}></div>
      <div className="absolute -bottom-8 left-20 w-72 h-72 bg-pink-500 rounded-full mix-blend-multiply filter blur-xl opacity-10 animate-float" style={{ animationDelay: '4s' }}></div>
      
      <div className="absolute inset-0 z-[-1]">
        <svg
          className="absolute w-full h-full"
          viewBox="0 0 1440 320"
          xmlns="http://www.w3.org/2000/svg"
          preserveAspectRatio="none"
        >
          <path
            fill="rgba(139, 92, 246, 0.05)"
            fillOpacity="1"
            d="M0,32L48,37.3C96,43,192,53,288,80C384,107,480,149,576,149.3C672,149,768,107,864,101.3C960,96,1056,128,1152,133.3C1248,139,1344,117,1392,106.7L1440,96L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z"
            className="animate-float"
            style={{ animationDelay: '0s' }}
          >
          </path>
          <path
            fill="rgba(236, 72, 153, 0.05)"
            fillOpacity="1"
            d="M0,224L48,224C96,224,192,224,288,197.3C384,171,480,117,576,117.3C672,117,768,171,864,197.3C960,224,1056,224,1152,208C1248,192,1344,160,1392,144L1440,128L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z"
            className="animate-float"
            style={{ animationDelay: '2s' }}
          >
          </path>
          <path
            fill="rgba(234, 179, 8, 0.05)"
            fillOpacity="1"
            d="M0,96L48,112C96,128,192,160,288,165.3C384,171,480,149,576,128C672,107,768,85,864,90.7C960,96,1056,128,1152,133.3C1248,139,1344,117,1392,106.7L1440,96L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z"
            className="animate-float"
            style={{ animationDelay: '4s' }}
          >
          </path>
        </svg>
      </div>

      <div className="absolute w-full h-full bg-[linear-gradient(to_bottom,transparent_0%,rgba(0,0,0,0.5)_100%)]"></div>
    </div>
  );
};

export default Background;
